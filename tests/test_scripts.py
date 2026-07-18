from __future__ import annotations

import subprocess
import sys


def test_all_sponsors_script_prints_three_arm_experiment(tmp_path):
    completed = subprocess.run(
        [sys.executable, "scripts/poc_all_sponsors.py"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        env={"ARCHON_STATE_PATH": str(tmp_path / "calibration.json")},
    )

    assert completed.returncode == 0
    assert "archon sponsor integration matrix" in completed.stdout
    assert "looped-Opus baseline: 4 iteration(s)" in completed.stdout
    assert "archon-warm:        2 iteration(s)" in completed.stdout
