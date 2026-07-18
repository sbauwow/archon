from archon.sandbox import (
    DirectSandbox,
    OpenShellSandbox,
    SandboxResult,
    build_sandbox_from_env,
)


def recording_runner(log):
    def run(argv):
        log.append(list(argv))
        return SandboxResult(0, stdout="ok")

    return run


def test_direct_sandbox_passes_argv_through():
    log = []
    result = DirectSandbox(runner=recording_runner(log)).run(["awslocal", "s3", "ls"])
    assert result.ok
    assert log == [["awslocal", "s3", "ls"]]


def test_openshell_sandbox_wraps_with_policy():
    log = []
    sandbox = OpenShellSandbox(policy_path="policies/deploy.openshell.yaml",
                               runner=recording_runner(log))
    sandbox.run(["awslocal", "ecs", "create-service"])
    assert log[0][:4] == ["openshell", "run", "--policy", "policies/deploy.openshell.yaml"]
    assert log[0][4] == "--"
    assert log[0][5:] == ["awslocal", "ecs", "create-service"]


def test_build_sandbox_from_env():
    assert isinstance(build_sandbox_from_env({}), DirectSandbox)
    sandbox = build_sandbox_from_env({"ARCHON_SANDBOX": "openshell",
                                      "ARCHON_SANDBOX_POLICY": "p.yaml"})
    assert isinstance(sandbox, OpenShellSandbox)
    assert sandbox.policy_path == "p.yaml"
