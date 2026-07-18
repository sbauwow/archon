"""Three-arm experiment for archon's core existence claim.

The fair baseline is not Opus one-shot. It is looped-Opus: the same propose →
deploy → measure → adjust loop, but with no cross-run calibration memory. Archon
only wins if warm, persisted environment calibration reduces iterations on a
held-out app shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent import ArchonAgent
from .cloud import SimulatedCloud
from .llm import StubArchitect
from .memory import CalibrationStore, JsonBackend
from .security import PatternScanner
from .spec import Intent


@dataclass(frozen=True)
class ArmResult:
    intent: str
    iterations: int
    converged: bool


@dataclass(frozen=True)
class ThreeArmReport:
    looped_opus: list[ArmResult]
    archon_cold: list[ArmResult]
    archon_warm: list[ArmResult]

    @property
    def delta_iterations_saved(self) -> int:
        baseline = sum(r.iterations for r in self.looped_opus)
        warm = sum(r.iterations for r in self.archon_warm)
        return baseline - warm


def run_three_arm_experiment(
    *,
    seed_intents: list[Intent],
    held_out_intents: list[Intent],
    state_dir: str | Path,
) -> ThreeArmReport:
    """Run looped-Opus, archon-cold, and archon-warm on held-out intents.

    looped-Opus and archon-cold are intentionally equivalent in this POC: both
    get the same measurement loop and no prior cross-run memory. The only thing
    archon-warm adds is calibration learned from seed intents and persisted into
    the held-out run.
    """
    state_dir = Path(state_dir)

    looped_opus = [_run_stateless(intent, state_dir / f"looped-{intent.name}.json") for intent in held_out_intents]
    archon_cold = [_run_stateless(intent, state_dir / f"cold-{intent.name}.json") for intent in held_out_intents]

    warm_path = state_dir / "warm.json"
    warm_store = CalibrationStore(backend=JsonBackend(str(warm_path)))
    warm_agent = _agent(warm_store)
    for intent in seed_intents:
        warm_agent.converge(intent)

    reloaded_warm_store = CalibrationStore(backend=JsonBackend(str(warm_path)))
    warm_agent = _agent(reloaded_warm_store)
    archon_warm = [_arm_result(intent, warm_agent.converge(intent)) for intent in held_out_intents]

    return ThreeArmReport(
        looped_opus=looped_opus,
        archon_cold=archon_cold,
        archon_warm=archon_warm,
    )


def summarize_experiment(report: ThreeArmReport) -> str:
    baseline = sum(r.iterations for r in report.looped_opus)
    cold = sum(r.iterations for r in report.archon_cold)
    warm = sum(r.iterations for r in report.archon_warm)
    return "\n".join(
        [
            "archon three-arm experiment",
            f"looped-Opus baseline: {baseline} iteration(s)",
            f"archon-cold:         {cold} iteration(s)",
            f"archon-warm:        {warm} iteration(s)",
            f"saved {report.delta_iterations_saved} deploy-measure-adjust cycle(s)",
            "warm wins only because it reuses persisted calibration; if that line disappears, archon ties looped-Opus.",
        ]
    )


def _run_stateless(intent: Intent, path: Path) -> ArmResult:
    store = CalibrationStore(backend=JsonBackend(str(path)))
    return _arm_result(intent, _agent(store).converge(intent))


def _agent(store: CalibrationStore) -> ArchonAgent:
    return ArchonAgent(
        architect=StubArchitect(),
        cloud=SimulatedCloud(),
        scanner=PatternScanner(),
        store=store,
    )


def _arm_result(intent: Intent, result) -> ArmResult:
    return ArmResult(intent=intent.name, iterations=result.iterations, converged=result.converged)
