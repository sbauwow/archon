"""Containment seam for deploy commands (OpenShell bounty).

Every real infrastructure command the agent runs goes through a Sandbox:

- DirectSandbox    — dev default, runs argv via an injectable runner.
- OpenShellSandbox — wraps argv in `openshell run --policy <yaml> -- <argv>`,
                     pairing HiddenLayer (detect) with OpenShell (contain).

The simulated cloud never shells out, so tests exercise the seam with a
recording runner instead of real processes.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence

Runner = Callable[[Sequence[str]], "SandboxResult"]


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _subprocess_runner(argv: Sequence[str]) -> SandboxResult:
    proc = subprocess.run(list(argv), capture_output=True, text=True)
    return SandboxResult(proc.returncode, proc.stdout, proc.stderr)


class Sandbox(Protocol):
    def run(self, argv: Sequence[str]) -> SandboxResult: ...


@dataclass
class DirectSandbox:
    runner: Runner = field(default=_subprocess_runner)

    def run(self, argv: Sequence[str]) -> SandboxResult:
        return self.runner(argv)


@dataclass
class OpenShellSandbox:
    policy_path: str
    openshell_bin: str = "openshell"
    runner: Runner = field(default=_subprocess_runner)

    def run(self, argv: Sequence[str]) -> SandboxResult:
        wrapped = [self.openshell_bin, "run", "--policy", self.policy_path, "--", *argv]
        return self.runner(wrapped)


DEFAULT_POLICY = str(Path(__file__).resolve().parent.parent / "policies" / "deploy.openshell.yaml")


def build_sandbox_from_env(env: dict[str, str] | None = None) -> Sandbox:
    """ARCHON_SANDBOX=openshell → OpenShellSandbox (policy via ARCHON_SANDBOX_POLICY)."""
    env = env if env is not None else dict(os.environ)
    if env.get("ARCHON_SANDBOX", "").lower() == "openshell":
        return OpenShellSandbox(policy_path=env.get("ARCHON_SANDBOX_POLICY", DEFAULT_POLICY))
    return DirectSandbox()
