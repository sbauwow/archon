"""Security screening: untrusted intent + every deploy action go through a Scanner.

- PatternScanner     — offline default; regex heuristics for the vivid threat
                       class (exfiltrate secrets, expose DB publicly, dump tables).
- HiddenLayerScanner — HiddenLayer Runtime Security API (event code AITX-2026),
                       transport-injectable so tests never touch the network.

The agent blocks on flagged intent (before any propose) and on flagged deploy
actions (before any execute). Findings are returned, not raised — the caller
decides policy.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .spec import DeployAction


@dataclass(frozen=True)
class Finding:
    flagged: bool
    reason: str = ""
    source: str = ""  # "intent" or the action kind


class Scanner(Protocol):
    def scan_intent(self, text: str) -> Finding: ...

    def scan_action(self, action: DeployAction) -> Finding: ...


_ATTACK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"expose\s+.*(publicly|0\.0\.0\.0|internet)", "exposes a private service publicly"),
    (r"(post|send|curl|upload).{0,40}(secret|credential|token|env)", "exfiltrates secrets"),
    (r"dump\s+.*(table|users|database)", "dumps data"),
    (r"disable\s+.*(auth|firewall|security)", "disables a security control"),
    (r"0\.0\.0\.0/0", "world-open network rule"),
)


class PatternScanner:
    """Offline heuristic scanner. Good enough for tests and the stub demo."""

    def scan_intent(self, text: str) -> Finding:
        return self._scan(text, source="intent")

    def scan_action(self, action: DeployAction) -> Finding:
        return self._scan(action.describe(), source=action.kind)

    def _scan(self, text: str, source: str) -> Finding:
        lowered = text.lower()
        for pattern, reason in _ATTACK_PATTERNS:
            if re.search(pattern, lowered):
                return Finding(flagged=True, reason=reason, source=source)
        return Finding(flagged=False, source=source)


Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


@dataclass
class HiddenLayerScanner:
    """HiddenLayer Runtime Security. Falls back to PatternScanner on API error —
    fail-closed on scan errors would brick the demo; fail-open would be dishonest;
    falling back to the local heuristic is the middle ground."""

    api_key: str
    endpoint: str = "https://api.hiddenlayer.ai/api/v1/submit"
    event_code: str = "AITX-2026"
    transport: Transport | None = None
    fallback: PatternScanner = field(default_factory=PatternScanner)

    def scan_intent(self, text: str) -> Finding:
        return self._scan(text, source="intent")

    def scan_action(self, action: DeployAction) -> Finding:
        return self._scan(action.describe(), source=action.kind)

    def _scan(self, text: str, source: str) -> Finding:
        payload = {"input": text, "event_code": self.event_code, "metadata": {"source": source}}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        try:
            body = self._send(self.endpoint, headers, payload)
        except Exception:
            return self.fallback._scan(text, source)
        flagged = bool(body.get("flagged") or body.get("detected"))
        return Finding(flagged=flagged, reason=str(body.get("reason", "")), source=source)

    def _send(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(url, headers, payload)
        import httpx

        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()


def build_scanner_from_env(env: dict[str, str] | None = None) -> Scanner:
    env = env if env is not None else dict(os.environ)
    key = env.get("HIDDENLAYER_API_KEY", "")
    if key:
        return HiddenLayerScanner(
            api_key=key,
            endpoint=env.get("HIDDENLAYER_ENDPOINT", HiddenLayerScanner.endpoint),
        )
    return PatternScanner()
