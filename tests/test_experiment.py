from archon.experiment import ArmResult, run_three_arm_experiment, summarize_experiment
from archon.spec import Intent, Target


SAAS = Intent(
    name="saas-api",
    description="SaaS backend with auth, Postgres, and a worker",
    target=Target(p95_ms=200, rps=100, monthly_cost=60),
)
HELD_OUT = Intent(
    name="realtime-dashboard",
    description="Realtime metrics dashboard with websocket fan-out",
    target=Target(p95_ms=150, rps=200, monthly_cost=120),
)


def test_three_arm_experiment_shows_warm_memory_delta(tmp_path):
    report = run_three_arm_experiment(
        seed_intents=[SAAS],
        held_out_intents=[HELD_OUT],
        state_dir=tmp_path,
    )

    assert report.looped_opus == [ArmResult(intent="realtime-dashboard", iterations=4, converged=True)]
    assert report.archon_cold == [ArmResult(intent="realtime-dashboard", iterations=4, converged=True)]
    assert report.archon_warm == [ArmResult(intent="realtime-dashboard", iterations=2, converged=True)]
    assert report.delta_iterations_saved == 2


def test_experiment_summary_is_demo_ready(tmp_path):
    report = run_three_arm_experiment(
        seed_intents=[SAAS],
        held_out_intents=[HELD_OUT],
        state_dir=tmp_path,
    )

    summary = summarize_experiment(report)

    assert "looped-Opus baseline: 4 iteration(s)" in summary
    assert "archon-warm:        2 iteration(s)" in summary
    assert "saved 2 deploy-measure-adjust cycle(s)" in summary
    assert "warm wins only because it reuses persisted calibration" in summary
