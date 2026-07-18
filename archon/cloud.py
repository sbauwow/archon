"""Deploy substrates.

- SimulatedCloud  — deterministic environment with *hidden* per-env truth the
                    spec sheet doesn't know: real capacity is a fraction of
                    documented, the bill runs above list price (egress), there's
                    a fixed latency overhead, and broad IAM policies are denied
                    (org policy). This is the archon thesis in miniature: the
                    architect's predictions are systematically wrong, and only
                    deploying + measuring reveals by how much.
- LocalStackCloud — real-command seam: emits awslocal/aws CLI actions through
                    the sandbox. Deploy works against a running LocalStack;
                    measurement there is out of POC scope.

Both speak the same protocol: plan_actions() → deploy() → observe().
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Protocol

from .llm import CACHE_LOAD_FACTOR, CACHE_P95_CUT_MS, CACHE_MONTHLY_COST, SPEC_SHEET
from .sandbox import DirectSandbox, Sandbox, SandboxResult
from .spec import DeployAction, Measurement, Proposal


class IAMDenied(Exception):
    """The environment's org policy rejected the deploy (AccessDenied)."""


@dataclass(frozen=True)
class Deployment:
    deployment_id: str
    proposal: Proposal
    url: str


class Cloud(Protocol):
    def plan_actions(self, proposal: Proposal) -> list[DeployAction]: ...

    def deploy(self, proposal: Proposal) -> Deployment: ...

    def observe(self, deployment: Deployment, rps: float) -> Measurement: ...


def _null_runner(argv) -> SandboxResult:  # simulated commands never hit a real shell
    return SandboxResult(0, stdout=f"simulated: {' '.join(argv)}")


@dataclass
class SimulatedCloud:
    """Hidden environment truth. The agent must discover these numbers by measuring."""

    throughput_ratio: float = 0.6  # real capacity vs. documented
    cost_ratio: float = 1.7  # real bill vs. list price (egress, requests)
    p95_offset_ms: float = 40.0  # env overhead (cold starts, cross-AZ hops)
    required_iam_policy: str = "scoped"  # org policy denies anything broader
    overload_penalty_ms: float = 500.0
    sandbox: Sandbox = field(default_factory=lambda: DirectSandbox(runner=_null_runner))
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1), repr=False)

    def plan_actions(self, proposal: Proposal) -> list[DeployAction]:
        actions = [
            DeployAction(
                kind="create-service",
                argv=(
                    "cloud", "create-service", "--size", proposal.instance_size,
                    "--replicas", str(proposal.replicas),
                    "--iam-policy", proposal.iam_policy,
                ),
            ),
        ]
        if proposal.cache:
            actions.append(DeployAction(kind="create-cache", argv=("cloud", "create-cache")))
        actions.append(
            DeployAction(kind="run-migration", argv=("cloud", "run-migration", "--db", "app"))
        )
        return actions

    def deploy(self, proposal: Proposal) -> Deployment:
        if proposal.iam_policy != self.required_iam_policy:
            raise IAMDenied(
                f"AccessDenied: org policy requires '{self.required_iam_policy}' "
                f"iam policy, got '{proposal.iam_policy}'"
            )
        for action in self.plan_actions(proposal):
            result = self.sandbox.run(action.argv)
            if not result.ok:
                raise RuntimeError(f"deploy action failed: {action.describe()}")
        dep_id = f"sim-{next(self._ids)}"
        return Deployment(deployment_id=dep_id, proposal=proposal, url=f"https://{dep_id}.sim.local")

    def observe(self, deployment: Deployment, rps: float) -> Measurement:
        p = deployment.proposal
        spec = SPEC_SHEET[p.instance_size]
        capacity = spec["capacity_rps"] * self.throughput_ratio * p.replicas
        effective_rps = rps * (CACHE_LOAD_FACTOR if p.cache else 1.0)

        p95 = spec["base_p95_ms"] + self.p95_offset_ms
        if p.cache:
            p95 = max(1.0, p95 - CACHE_P95_CUT_MS)
        overload = effective_rps / capacity
        if overload > 1.0:
            p95 += (overload - 1.0) * self.overload_penalty_ms
        error_rate = 0.05 if overload > 1.5 else 0.0

        cost = spec["monthly_cost"] * p.replicas
        if p.cache:
            cost += CACHE_MONTHLY_COST
        cost *= self.cost_ratio

        throughput = min(rps, capacity / (CACHE_LOAD_FACTOR if p.cache else 1.0))
        return Measurement(
            p95_ms=round(p95, 2),
            throughput_rps=round(throughput, 2),
            error_rate=error_rate,
            monthly_cost=round(cost, 2),
        )


@dataclass
class LocalStackCloud:
    """AWS-shaped deploys against LocalStack via awslocal, contained by the sandbox.

    POC scope: deploy issues real CLI commands; observe() is not implemented here
    (live load generation is the next build phase — see README build plan Phase 0).
    """

    sandbox: Sandbox = field(default_factory=DirectSandbox)
    cli: str = "awslocal"

    def plan_actions(self, proposal: Proposal) -> list[DeployAction]:
        return [
            DeployAction(
                kind="create-security-group",
                argv=(self.cli, "ec2", "create-security-group",
                      "--group-name", "archon-app", "--description", "archon app sg"),
            ),
            DeployAction(
                kind="create-service",
                argv=(self.cli, "ecs", "create-service", "--service-name", "archon-app",
                      "--desired-count", str(proposal.replicas)),
            ),
        ]

    def deploy(self, proposal: Proposal) -> Deployment:
        for action in self.plan_actions(proposal):
            result = self.sandbox.run(action.argv)
            if not result.ok:
                if "AccessDenied" in (result.stderr or ""):
                    raise IAMDenied(result.stderr)
                raise RuntimeError(f"deploy action failed: {action.describe()}\n{result.stderr}")
        return Deployment(deployment_id="localstack-archon-app", proposal=proposal,
                          url="http://localhost:4566")

    def observe(self, deployment: Deployment, rps: float) -> Measurement:
        raise NotImplementedError("live measurement against LocalStack is post-POC")
