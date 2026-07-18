import pytest

from archon.spec import DeployAction, Measurement, Proposal, Target


def test_measurement_meets_target():
    target = Target(p95_ms=200, rps=100, monthly_cost=50)
    good = Measurement(p95_ms=150, throughput_rps=100, error_rate=0.0, monthly_cost=40)
    assert good.meets(target)


@pytest.mark.parametrize(
    "p95,cost,err",
    [(250, 40, 0.0), (150, 60, 0.0), (150, 40, 0.05)],
)
def test_measurement_fails_target_on_any_axis(p95, cost, err):
    target = Target(p95_ms=200, rps=100, monthly_cost=50)
    assert not Measurement(p95_ms=p95, throughput_rps=100, error_rate=err, monthly_cost=cost).meets(target)


def test_proposal_validates_size_and_replicas():
    with pytest.raises(ValueError):
        Proposal("xlarge", 1, False, "broad", 0, 0)
    with pytest.raises(ValueError):
        Proposal("small", 0, False, "broad", 0, 0)


def test_deploy_action_describe():
    action = DeployAction(kind="set-env", argv=("cloud", "set-env", "KEY=1"))
    assert action.describe() == "set-env: cloud set-env KEY=1"
