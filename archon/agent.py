"""The convergence loop: propose → screen → deploy → measure → learn → adjust.

Inner loop (one app): deploy, drive load, read measured p95/cost/errors, learn
calibration, re-propose until the target is met. Iterations-to-target is the
headline metric.

Outer loop (across apps): the CalibrationStore persists, so the next app's
first proposal is already corrected by this environment's measured truth —
iterations-to-target drops run over run. That drop is the whole project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .cloud import Cloud, IAMDenied
from .llm import Architect
from .memory import CalibrationStore
from .security import Finding, Scanner
from .spec import Intent, Measurement, Proposal


@dataclass
class Attempt:
    proposal: Proposal
    outcome: str  # "iam-denied", "missed-target", "converged", "blocked"
    measurement: Measurement | None = None
    detail: str = ""


@dataclass
class ConvergenceResult:
    intent: Intent
    converged: bool
    blocked: bool = False
    block_reason: str = ""
    iterations: int = 0  # deploy attempts, including failed ones
    proposal: Proposal | None = None
    measurement: Measurement | None = None
    history: list[Attempt] = field(default_factory=list)


def _bump(proposal: Proposal, hints: dict[str, Any]) -> Proposal:
    """Forced progress when the architect re-proposes a shape already tried:
    one discrete architectural step up."""
    from .llm import predict
    from .spec import INSTANCE_SIZES

    size, replicas, cache = proposal.instance_size, proposal.replicas, proposal.cache
    if not cache:
        cache = True
    elif replicas < 6:
        replicas += 1
    elif size != INSTANCE_SIZES[-1]:
        size = INSTANCE_SIZES[INSTANCE_SIZES.index(size) + 1]
        replicas = 1
    p95, cost = predict(size, replicas, cache, rps=1.0, hints=hints)
    return Proposal(
        instance_size=size, replicas=replicas, cache=cache,
        iam_policy=proposal.iam_policy,
        predicted_p95_ms=p95, predicted_monthly_cost=cost,
    )


@dataclass
class ArchonAgent:
    architect: Architect
    cloud: Cloud
    scanner: Scanner
    store: CalibrationStore
    max_iterations: int = 8

    def converge(self, intent: Intent) -> ConvergenceResult:
        result = ConvergenceResult(intent=intent, converged=False)

        finding = self.scanner.scan_intent(intent.description)
        if finding.flagged:
            result.blocked = True
            result.block_reason = f"intent flagged: {finding.reason}"
            result.history.append(
                Attempt(proposal=None, outcome="blocked", detail=result.block_reason)  # type: ignore[arg-type]
            )
            return result

        tried: set[tuple[str, int, bool]] = set()
        proposal = self.architect.propose(intent, self.store.hints())

        while result.iterations < self.max_iterations:
            action_finding = self._screen_actions(proposal)
            if action_finding is not None:
                result.blocked = True
                result.block_reason = f"deploy action flagged: {action_finding.reason}"
                result.history.append(
                    Attempt(proposal=proposal, outcome="blocked", detail=result.block_reason)
                )
                return result

            result.iterations += 1
            try:
                deployment = self.cloud.deploy(proposal)
            except IAMDenied as exc:
                accepted = self._accepted_policy(str(exc))
                self.store.learn_iam_denial(accepted)
                result.history.append(
                    Attempt(proposal=proposal, outcome="iam-denied", detail=str(exc))
                )
                proposal = self._next_proposal(intent, tried)
                continue

            measurement = self.cloud.observe(deployment, intent.target.rps)
            self.store.learn_measurement(proposal, measurement, intent.target)

            if measurement.meets(intent.target):
                result.history.append(
                    Attempt(proposal=proposal, outcome="converged", measurement=measurement)
                )
                result.converged = True
                result.proposal = proposal
                result.measurement = measurement
                return result

            # only a *measured* miss rules a shape out — an IAM-denied attempt
            # says nothing about the shape itself
            tried.add((proposal.instance_size, proposal.replicas, proposal.cache))
            result.history.append(
                Attempt(proposal=proposal, outcome="missed-target", measurement=measurement,
                        detail=f"p95={measurement.p95_ms}ms cost=${measurement.monthly_cost}"
                               f" err={measurement.error_rate}")
            )
            proposal = self._next_proposal(intent, tried)

        result.proposal = proposal
        return result

    # -- helpers -----------------------------------------------------------
    def _screen_actions(self, proposal: Proposal) -> Finding | None:
        for action in self.cloud.plan_actions(proposal):
            finding = self.scanner.scan_action(action)
            if finding.flagged:
                return finding
        return None

    def _next_proposal(self, intent: Intent, tried: set[tuple[str, int, bool]]) -> Proposal:
        hints = self.store.hints()
        proposal = self.architect.propose(intent, hints)
        if (proposal.instance_size, proposal.replicas, proposal.cache) in tried:
            proposal = _bump(proposal, hints)
        return proposal

    @staticmethod
    def _accepted_policy(message: str) -> str:
        match = re.search(r"requires '(\w+)'", message)
        return match.group(1) if match else "scoped"
