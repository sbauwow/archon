"""Core value types: intent, target, proposal, measurement.

Everything downstream (architect, cloud, measurement, calibration) speaks
these types. Keep them dumb dataclasses — no behavior beyond validation
and serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

INSTANCE_SIZES = ("small", "medium", "large")


@dataclass(frozen=True)
class Target:
    """A measurable cost/perf target: p95 under `p95_ms` at `rps`, under `monthly_cost` USD."""

    p95_ms: float
    rps: float
    monthly_cost: float


@dataclass(frozen=True)
class Intent:
    """What the user wants built, plus the target it must hit."""

    name: str
    description: str
    target: Target
    # Feature vector the calibration layer keys on (workload shape, not app identity).
    features: dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    """An architecture the agent intends to deploy, with its *predicted* numbers.

    Predictions come from spec sheets / the architect model; the whole point of
    archon is that predictions are systematically wrong per-environment and the
    measured truth corrects them.
    """

    instance_size: str
    replicas: int
    cache: bool
    iam_policy: str  # "broad" (naive default, envs often deny) or "scoped"
    predicted_p95_ms: float
    predicted_monthly_cost: float

    def __post_init__(self) -> None:
        if self.instance_size not in INSTANCE_SIZES:
            raise ValueError(f"unknown instance size: {self.instance_size}")
        if self.replicas < 1:
            raise ValueError("replicas must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Measurement:
    """Ground truth read back from a deployed system under load. The oracle."""

    p95_ms: float
    throughput_rps: float
    error_rate: float
    monthly_cost: float

    def meets(self, target: Target) -> bool:
        return (
            self.p95_ms <= target.p95_ms
            and self.monthly_cost <= target.monthly_cost
            and self.error_rate < 0.01
        )


@dataclass(frozen=True)
class DeployAction:
    """One concrete infrastructure change, screened by security before execution."""

    kind: str  # e.g. "create-service", "set-env", "run-migration"
    argv: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.kind}: {' '.join(self.argv)}"
