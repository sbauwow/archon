"""Architect brains: turn an intent + calibration hints into a first Proposal.

Three implementations behind one protocol:

- StubArchitect      — deterministic spec-sheet search; the dev/test default,
                       and the honest "what a model predicts from public specs"
                       baseline (it believes list prices and documented capacity).
- OpenAICompatBrain  — local NVIDIA model (Nemotron on vLLM, guided JSON) or any
                       OpenAI-compatible endpoint. Transport-injectable.
- AnthropicBrain     — Claude escalation tier. Transport-injectable.

The LLM brains delegate the *numeric* prediction to the same spec-sheet math as
the stub (models are bad at arithmetic; the environment is wrong about specs
either way — that's what calibration corrects). What the LLM contributes is the
architectural shape choice, returned as guided JSON.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .spec import INSTANCE_SIZES, Intent, Proposal

# Public "spec sheet" numbers an architect believes before measuring anything:
# per-instance capacity (rps), monthly list cost (USD), base p95 (ms).
SPEC_SHEET: dict[str, dict[str, float]] = {
    "small": {"capacity_rps": 50.0, "monthly_cost": 5.0, "base_p95_ms": 80.0},
    "medium": {"capacity_rps": 150.0, "monthly_cost": 15.0, "base_p95_ms": 60.0},
    "large": {"capacity_rps": 400.0, "monthly_cost": 40.0, "base_p95_ms": 50.0},
}
CACHE_MONTHLY_COST = 10.0
CACHE_P95_CUT_MS = 20.0
CACHE_LOAD_FACTOR = 0.5  # cache serves half the traffic

# Calibration hint keys (written by CalibrationStore, read here).
HINT_THROUGHPUT_RATIO = "throughput_ratio"  # measured capacity / spec capacity
HINT_COST_RATIO = "cost_ratio"  # measured bill / list price
HINT_P95_OFFSET = "p95_offset_ms"  # env overhead on top of base p95
HINT_IAM_POLICY = "iam_policy"  # policy shape this env actually accepts

OVERLOAD_PENALTY_MS = 500.0  # p95 penalty per 100% overload, mirrors cloud sim shape


def predict(
    size: str,
    replicas: int,
    cache: bool,
    rps: float,
    hints: dict[str, Any] | None = None,
) -> tuple[float, float]:
    """Spec-sheet prediction of (p95_ms, monthly_cost), corrected by calibration hints."""
    hints = hints or {}
    spec = SPEC_SHEET[size]
    throughput_ratio = float(hints.get(HINT_THROUGHPUT_RATIO, 1.0))
    cost_ratio = float(hints.get(HINT_COST_RATIO, 1.0))
    p95_offset = float(hints.get(HINT_P95_OFFSET, 0.0))

    capacity = spec["capacity_rps"] * throughput_ratio * replicas
    effective_rps = rps * (CACHE_LOAD_FACTOR if cache else 1.0)
    p95 = spec["base_p95_ms"] + p95_offset
    if cache:
        p95 = max(1.0, p95 - CACHE_P95_CUT_MS)
    if effective_rps > capacity:
        p95 += (effective_rps / capacity - 1.0) * OVERLOAD_PENALTY_MS

    cost = spec["monthly_cost"] * replicas * cost_ratio
    if cache:
        cost += CACHE_MONTHLY_COST * cost_ratio
    return p95, cost


def _candidate_shapes(max_replicas: int = 6) -> list[tuple[str, int, bool]]:
    return [
        (size, replicas, cache)
        for size in INSTANCE_SIZES
        for replicas in range(1, max_replicas + 1)
        for cache in (False, True)
    ]


def cheapest_meeting_target(intent: Intent, hints: dict[str, Any] | None = None) -> Proposal:
    """Cheapest predicted-to-pass shape; falls back to best-effort if nothing passes."""
    hints = hints or {}
    target = intent.target
    best: tuple[float, Proposal] | None = None
    fallback: tuple[float, Proposal] | None = None
    for size, replicas, cache in _candidate_shapes():
        p95, cost = predict(size, replicas, cache, target.rps, hints)
        proposal = Proposal(
            instance_size=size,
            replicas=replicas,
            cache=cache,
            iam_policy=str(hints.get(HINT_IAM_POLICY, "broad")),
            predicted_p95_ms=p95,
            predicted_monthly_cost=cost,
        )
        if p95 <= target.p95_ms and cost <= target.monthly_cost:
            if best is None or cost < best[0]:
                best = (cost, proposal)
        # best-effort fallback: minimize p95 miss, then cost
        miss = max(0.0, p95 - target.p95_ms)
        if fallback is None or (miss, cost) < fallback[0:1] + (fallback[1].predicted_monthly_cost,):
            fallback = (miss, proposal)
    if best is not None:
        return best[1]
    assert fallback is not None
    return fallback[1]


class Architect(Protocol):
    def propose(self, intent: Intent, hints: dict[str, Any] | None = None) -> Proposal: ...


class StubArchitect:
    """Deterministic spec-sheet architect. Believes list prices and docs."""

    def propose(self, intent: Intent, hints: dict[str, Any] | None = None) -> Proposal:
        return cheapest_meeting_target(intent, hints)


Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]

SHAPE_SCHEMA = {
    "type": "object",
    "properties": {
        "instance_size": {"type": "string", "enum": list(INSTANCE_SIZES)},
        "replicas": {"type": "integer", "minimum": 1, "maximum": 6},
        "cache": {"type": "boolean"},
    },
    "required": ["instance_size", "replicas", "cache"],
}


def _shape_prompt(intent: Intent, hints: dict[str, Any]) -> str:
    return (
        "You are a cloud solutions architect. Pick a deployment shape as JSON "
        f"matching this schema: {json.dumps(SHAPE_SCHEMA)}.\n"
        f"App: {intent.description}\n"
        f"Target: p95<={intent.target.p95_ms}ms at {intent.target.rps}rps, "
        f"<= ${intent.target.monthly_cost}/mo.\n"
        f"Spec sheet: {json.dumps(SPEC_SHEET)}\n"
        f"Environment calibration (trust these over the spec sheet): {json.dumps(hints)}\n"
        "Respond with JSON only."
    )


def _proposal_from_shape(shape: dict[str, Any], intent: Intent, hints: dict[str, Any]) -> Proposal:
    size = shape["instance_size"]
    replicas = int(shape["replicas"])
    cache = bool(shape["cache"])
    p95, cost = predict(size, replicas, cache, intent.target.rps, hints)
    return Proposal(
        instance_size=size,
        replicas=replicas,
        cache=cache,
        iam_policy=str(hints.get(HINT_IAM_POLICY, "broad")),
        predicted_p95_ms=p95,
        predicted_monthly_cost=cost,
    )


@dataclass
class OpenAICompatBrain:
    """Nemotron-on-vLLM (or any OpenAI-compatible endpoint) picks the shape.

    Uses guided JSON (`response_format`) so a small model can't fail on format.
    Falls back to the stub search if the endpoint errors.
    """

    endpoint: str
    model: str = "nvidia/Nemotron-Mini-4B-Instruct"
    api_key: str = ""
    transport: Transport | None = None
    fallback: StubArchitect = field(default_factory=StubArchitect)

    def propose(self, intent: Intent, hints: dict[str, Any] | None = None) -> Proposal:
        hints = hints or {}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": _shape_prompt(intent, hints)}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "shape", "schema": SHAPE_SCHEMA},
            },
            "temperature": 0,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            body = self._send(self.endpoint, headers, payload)
            shape = json.loads(body["choices"][0]["message"]["content"])
            return _proposal_from_shape(shape, intent, hints)
        except Exception:
            return self.fallback.propose(intent, hints)

    def _send(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(url, headers, payload)
        import httpx

        resp = httpx.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()


@dataclass
class AnthropicBrain:
    """Claude escalation architect. Same shape contract as the local brain."""

    model: str = "claude-fable-5"
    api_key: str = ""
    transport: Transport | None = None
    fallback: StubArchitect = field(default_factory=StubArchitect)

    ENDPOINT = "https://api.anthropic.com/v1/messages"

    def propose(self, intent: Intent, hints: dict[str, Any] | None = None) -> Proposal:
        hints = hints or {}
        payload = {
            "model": self.model,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": _shape_prompt(intent, hints)}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            body = self._send(self.ENDPOINT, headers, payload)
            shape = json.loads(body["content"][0]["text"])
            return _proposal_from_shape(shape, intent, hints)
        except Exception:
            return self.fallback.propose(intent, hints)

    def _send(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(url, headers, payload)
        import httpx

        resp = httpx.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()


def build_architect_from_env(env: dict[str, str] | None = None) -> Architect:
    """ARCHON_LOCAL_ENDPOINT → local Nemotron brain; ANTHROPIC_API_KEY → Claude; else stub."""
    env = env if env is not None else dict(os.environ)
    if env.get("ARCHON_LOCAL_ENDPOINT"):
        return OpenAICompatBrain(
            endpoint=env["ARCHON_LOCAL_ENDPOINT"],
            model=env.get("ARCHON_LOCAL_MODEL", OpenAICompatBrain.model),
            api_key=env.get("ARCHON_LOCAL_API_KEY", ""),
        )
    if env.get("ANTHROPIC_API_KEY"):
        return AnthropicBrain(api_key=env["ANTHROPIC_API_KEY"])
    return StubArchitect()
