"""Persistent per-environment calibration memory — the recursive core.

archon does not memorize apps; it learns the *systematic offset* between what
the spec sheet predicts and what this environment actually delivers:

- throughput_ratio — measured capacity / documented capacity (learned when a
  deploy saturates; an unsaturated run carries no capacity signal)
- cost_ratio       — measured bill / list price
- p95_offset_ms    — measured p95 - predicted p95 (learned when not overloaded,
  otherwise the overload penalty pollutes the offset)
- iam_policy fact  — the policy shape this environment actually accepts

Ratios are workload-independent, so they transfer to the next app (the
calibration bet). Backends: JSON file (default) or Supabase Postgres
(PostgREST upsert, transport-injectable).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .llm import (
    CACHE_LOAD_FACTOR,
    HINT_COST_RATIO,
    HINT_IAM_POLICY,
    HINT_P95_OFFSET,
    HINT_THROUGHPUT_RATIO,
    SPEC_SHEET,
    predict,
)
from .spec import Measurement, Proposal, Target


class StateBackend(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, state: dict[str, Any]) -> None: ...


@dataclass
class JsonBackend:
    path: str

    def load(self) -> dict[str, Any]:
        p = Path(self.path)
        if not p.exists():
            return {}
        return json.loads(p.read_text())

    def save(self, state: dict[str, Any]) -> None:
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2))


Transport = Callable[[str, str, dict[str, str], dict[str, Any] | None], Any]


def _httpx_transport(method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None):
    import httpx

    resp = httpx.request(method, url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else None


@dataclass
class SupabaseBackend:
    """Calibration state in Supabase Postgres via PostgREST.

    Table: archon_calibration(env_id text primary key, state jsonb).
    """

    url: str
    key: str
    env_id: str = "default"
    table: str = "archon_calibration"
    transport: Transport = field(default=_httpx_transport)

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def load(self) -> dict[str, Any]:
        url = f"{self.url}/rest/v1/{self.table}?env_id=eq.{self.env_id}&select=state"
        rows = self.transport("GET", url, self._headers(), None)
        if rows:
            return rows[0].get("state") or {}
        return {}

    def save(self, state: dict[str, Any]) -> None:
        url = f"{self.url}/rest/v1/{self.table}"
        headers = self._headers() | {"Prefer": "resolution=merge-duplicates"}
        self.transport("POST", url, headers, {"env_id": self.env_id, "state": state})


@dataclass
class CalibrationStore:
    backend: StateBackend
    state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            self.state = self.backend.load() or {}
        except Exception:
            self.state = {}  # a flaky backend must not brick the agent
        self.state.setdefault("ratios", {})
        self.state.setdefault("facts", {})

    # -- reading ----------------------------------------------------------
    def hints(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, entry in self.state["ratios"].items():
            if entry.get("n", 0) > 0:
                out[name] = entry["value"]
        out.update(self.state["facts"])
        return out

    # -- learning ---------------------------------------------------------
    def learn_iam_denial(self, accepted_policy: str) -> None:
        self.state["facts"][HINT_IAM_POLICY] = accepted_policy
        self._persist()

    def learn_measurement(self, proposal: Proposal, measurement: Measurement, target: Target) -> None:
        """Update calibration ratios from one deploy's measured ground truth."""
        naive_p95, naive_cost = predict(
            proposal.instance_size, proposal.replicas, proposal.cache, target.rps, hints=None
        )
        self._update(HINT_COST_RATIO, measurement.monthly_cost / naive_cost)

        saturated = measurement.throughput_rps < target.rps
        if saturated:
            factor = CACHE_LOAD_FACTOR if proposal.cache else 1.0
            capacity = measurement.throughput_rps * factor
            documented = SPEC_SHEET[proposal.instance_size]["capacity_rps"] * proposal.replicas
            self._update(HINT_THROUGHPUT_RATIO, capacity / documented)
        else:
            # not overloaded → p95 gap is pure environment overhead
            self._update(HINT_P95_OFFSET, measurement.p95_ms - naive_p95)
        self._persist()

    def _update(self, name: str, observed: float) -> None:
        entry = self.state["ratios"].get(name, {"value": 0.0, "n": 0})
        n = entry["n"] + 1
        entry["value"] = entry["value"] + (observed - entry["value"]) / n  # running mean
        entry["n"] = n
        self.state["ratios"][name] = entry

    def _persist(self) -> None:
        try:
            self.backend.save(self.state)
        except Exception:
            pass  # persistence failure must not kill the convergence loop


def build_store_from_env(env: dict[str, str] | None = None) -> CalibrationStore:
    """SUPABASE_URL+SUPABASE_KEY → Supabase backend; else JSON at ARCHON_STATE_PATH."""
    env = env if env is not None else dict(os.environ)
    if env.get("SUPABASE_URL") and env.get("SUPABASE_KEY"):
        backend: StateBackend = SupabaseBackend(
            url=env["SUPABASE_URL"].rstrip("/"),
            key=env["SUPABASE_KEY"],
            env_id=env.get("ARCHON_ENV_ID", "default"),
        )
    else:
        backend = JsonBackend(env.get("ARCHON_STATE_PATH", "_archon_state/calibration.json"))
    return CalibrationStore(backend=backend)
