import pytest

from archon.cloud import IAMDenied, LocalStackCloud, SimulatedCloud
from archon.sandbox import DirectSandbox, SandboxResult
from archon.spec import Proposal


def proposal(size="small", replicas=2, cache=False, iam="scoped"):
    return Proposal(size, replicas, cache, iam, predicted_p95_ms=80, predicted_monthly_cost=10)


def test_simulated_cloud_denies_broad_iam():
    with pytest.raises(IAMDenied, match="requires 'scoped'"):
        SimulatedCloud().deploy(proposal(iam="broad"))


def test_simulated_cloud_deploys_scoped():
    deployment = SimulatedCloud().deploy(proposal())
    assert deployment.url.startswith("https://sim-")


def test_hidden_truth_diverges_from_spec_sheet():
    cloud = SimulatedCloud()
    dep = cloud.deploy(proposal())  # spec sheet says 2 smalls handle 100rps at p95=80
    m = cloud.observe(dep, rps=100)
    assert m.p95_ms > 200  # real capacity is 0.6× ⇒ overloaded
    assert m.monthly_cost == pytest.approx(10 * 1.7)
    assert m.throughput_rps == pytest.approx(60)  # saturated at real capacity


def test_adequately_provisioned_shape_meets_load():
    cloud = SimulatedCloud()
    dep = cloud.deploy(proposal(replicas=4))  # real capacity 4×30 = 120 ≥ 100
    m = cloud.observe(dep, rps=100)
    assert m.p95_ms == pytest.approx(80 + 40)
    assert m.error_rate == 0.0
    assert m.throughput_rps == pytest.approx(100)


def test_cache_halves_effective_load():
    cloud = SimulatedCloud()
    dep = cloud.deploy(proposal(replicas=2, cache=True))  # eff 50 vs capacity 60
    m = cloud.observe(dep, rps=100)
    assert m.p95_ms == pytest.approx(80 + 40 - 20)


def test_simulated_deploy_routes_actions_through_sandbox():
    log = []

    def runner(argv):
        log.append(list(argv))
        return SandboxResult(0)

    cloud = SimulatedCloud(sandbox=DirectSandbox(runner=runner))
    cloud.deploy(proposal(cache=True))
    kinds = [argv[1] for argv in log]
    assert kinds == ["create-service", "create-cache", "run-migration"]


def test_localstack_deploy_shells_awslocal_and_maps_access_denied():
    log = []

    def ok_runner(argv):
        log.append(list(argv))
        return SandboxResult(0)

    cloud = LocalStackCloud(sandbox=DirectSandbox(runner=ok_runner))
    dep = cloud.deploy(proposal())
    assert dep.url == "http://localhost:4566"
    assert log[0][0] == "awslocal"

    def denied_runner(argv):
        return SandboxResult(255, stderr="An error occurred (AccessDenied)")

    with pytest.raises(IAMDenied):
        LocalStackCloud(sandbox=DirectSandbox(runner=denied_runner)).deploy(proposal())
