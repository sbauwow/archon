import json

from archon.llm import (
    HINT_COST_RATIO,
    HINT_IAM_POLICY,
    HINT_THROUGHPUT_RATIO,
    AnthropicBrain,
    OpenAICompatBrain,
    StubArchitect,
    build_architect_from_env,
    predict,
)
from archon.spec import Intent, Target

INTENT = Intent(
    name="app",
    description="SaaS backend",
    target=Target(p95_ms=200, rps=100, monthly_cost=60),
)


def test_predict_applies_calibration_hints():
    naive_p95, naive_cost = predict("small", 2, False, rps=100)
    hinted_p95, hinted_cost = predict(
        "small", 2, False, rps=100,
        hints={HINT_THROUGHPUT_RATIO: 0.5, HINT_COST_RATIO: 2.0},
    )
    assert hinted_cost == naive_cost * 2.0
    assert hinted_p95 > naive_p95  # halved capacity ⇒ overload penalty appears


def test_stub_picks_cheapest_passing_shape():
    proposal = StubArchitect().propose(INTENT)
    assert proposal.predicted_p95_ms <= INTENT.target.p95_ms
    assert proposal.predicted_monthly_cost <= INTENT.target.monthly_cost
    # naive spec-sheet optimum for 100rps: two smalls, no cache
    assert (proposal.instance_size, proposal.replicas, proposal.cache) == ("small", 2, False)


def test_stub_uses_iam_hint():
    assert StubArchitect().propose(INTENT).iam_policy == "broad"
    hinted = StubArchitect().propose(INTENT, {HINT_IAM_POLICY: "scoped"})
    assert hinted.iam_policy == "scoped"


def test_calibrated_stub_proposes_bigger_shape():
    hints = {HINT_THROUGHPUT_RATIO: 0.6, HINT_COST_RATIO: 1.7}
    cold = StubArchitect().propose(INTENT)
    warm = StubArchitect().propose(INTENT, hints)
    cold_capacity = cold.replicas * (2 if cold.cache else 1)
    warm_capacity = warm.replicas * (2 if warm.cache else 1)
    assert warm_capacity > cold_capacity  # discounts documented capacity ⇒ provisions more


def test_openai_brain_uses_transport_and_guided_json():
    seen = {}

    def transport(url, headers, payload):
        seen["url"] = url
        seen["payload"] = payload
        return {"choices": [{"message": {"content": json.dumps(
            {"instance_size": "medium", "replicas": 2, "cache": True}
        )}}]}

    brain = OpenAICompatBrain(endpoint="http://gpu:8000/v1/chat/completions", transport=transport)
    proposal = brain.propose(INTENT)
    assert seen["url"] == "http://gpu:8000/v1/chat/completions"
    assert seen["payload"]["response_format"]["type"] == "json_schema"
    assert (proposal.instance_size, proposal.replicas, proposal.cache) == ("medium", 2, True)


def test_openai_brain_falls_back_to_stub_on_error():
    def transport(url, headers, payload):
        raise ConnectionError("endpoint down")

    proposal = OpenAICompatBrain(endpoint="http://gpu:8000", transport=transport).propose(INTENT)
    assert (proposal.instance_size, proposal.replicas, proposal.cache) == ("small", 2, False)


def test_anthropic_brain_transport():
    def transport(url, headers, payload):
        assert headers["x-api-key"] == "sk-test"
        return {"content": [{"text": json.dumps(
            {"instance_size": "large", "replicas": 1, "cache": False}
        )}]}

    proposal = AnthropicBrain(api_key="sk-test", transport=transport).propose(INTENT)
    assert proposal.instance_size == "large"


def test_build_architect_from_env():
    assert isinstance(build_architect_from_env({}), StubArchitect)
    local = build_architect_from_env({"ARCHON_LOCAL_ENDPOINT": "http://gpu:8000"})
    assert isinstance(local, OpenAICompatBrain)
    claude = build_architect_from_env({"ANTHROPIC_API_KEY": "sk"})
    assert isinstance(claude, AnthropicBrain)
