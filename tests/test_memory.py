import pytest

from archon.llm import HINT_COST_RATIO, HINT_IAM_POLICY, HINT_P95_OFFSET, HINT_THROUGHPUT_RATIO
from archon.memory import (
    CalibrationStore,
    JsonBackend,
    SupabaseBackend,
    build_store_from_env,
)
from archon.spec import Measurement, Proposal, Target

TARGET = Target(p95_ms=200, rps=100, monthly_cost=60)


def proposal(size="small", replicas=2, cache=False):
    return Proposal(size, replicas, cache, "scoped", predicted_p95_ms=80, predicted_monthly_cost=10)


def store(tmp_path):
    return CalibrationStore(backend=JsonBackend(str(tmp_path / "cal.json")))


def test_empty_store_has_no_hints(tmp_path):
    assert store(tmp_path).hints() == {}


def test_learns_iam_fact(tmp_path):
    s = store(tmp_path)
    s.learn_iam_denial("scoped")
    assert s.hints()[HINT_IAM_POLICY] == "scoped"


def test_learns_cost_and_throughput_ratio_from_saturated_run(tmp_path):
    s = store(tmp_path)
    # 2 smalls at real capacity 60 (documented 100), bill 17 (list 10)
    m = Measurement(p95_ms=453, throughput_rps=60, error_rate=0.05, monthly_cost=17)
    s.learn_measurement(proposal(), m, TARGET)
    hints = s.hints()
    assert hints[HINT_COST_RATIO] == pytest.approx(1.7)
    assert hints[HINT_THROUGHPUT_RATIO] == pytest.approx(0.6)
    assert HINT_P95_OFFSET not in hints  # overloaded run carries no clean offset signal


def test_learns_p95_offset_from_unsaturated_run(tmp_path):
    s = store(tmp_path)
    m = Measurement(p95_ms=120, throughput_rps=100, error_rate=0.0, monthly_cost=34)
    s.learn_measurement(proposal(replicas=4), m, TARGET)
    assert s.hints()[HINT_P95_OFFSET] == pytest.approx(120 - 80)


def test_ratios_are_running_means(tmp_path):
    s = store(tmp_path)
    m1 = Measurement(p95_ms=453, throughput_rps=60, error_rate=0.05, monthly_cost=17)
    m2 = Measurement(p95_ms=453, throughput_rps=60, error_rate=0.05, monthly_cost=20)
    s.learn_measurement(proposal(), m1, TARGET)
    s.learn_measurement(proposal(), m2, TARGET)
    assert s.hints()[HINT_COST_RATIO] == pytest.approx((1.7 + 2.0) / 2)


def test_persists_and_reloads(tmp_path):
    path = tmp_path / "cal.json"
    s = CalibrationStore(backend=JsonBackend(str(path)))
    s.learn_iam_denial("scoped")
    reloaded = CalibrationStore(backend=JsonBackend(str(path)))
    assert reloaded.hints()[HINT_IAM_POLICY] == "scoped"


def test_flaky_backend_does_not_brick_the_store():
    class ExplodingBackend:
        def load(self):
            raise ConnectionError("db down")

        def save(self, state):
            raise ConnectionError("db down")

    s = CalibrationStore(backend=ExplodingBackend())
    s.learn_iam_denial("scoped")  # save fails silently
    assert s.hints()[HINT_IAM_POLICY] == "scoped"  # in-memory state still works


def test_supabase_backend_upserts_and_reads():
    rows = {}

    def transport(method, url, headers, payload):
        if method == "GET":
            state = rows.get("default")
            return [{"state": state}] if state is not None else []
        assert headers["Prefer"] == "resolution=merge-duplicates"
        rows[payload["env_id"]] = payload["state"]
        return None

    backend = SupabaseBackend(url="https://x.supabase.co", key="k", transport=transport)
    s = CalibrationStore(backend=backend)
    s.learn_iam_denial("scoped")
    s2 = CalibrationStore(backend=SupabaseBackend(url="https://x.supabase.co", key="k",
                                                  transport=transport))
    assert s2.hints()[HINT_IAM_POLICY] == "scoped"


def test_build_store_from_env(tmp_path):
    s = build_store_from_env({"ARCHON_STATE_PATH": str(tmp_path / "cal.json")})
    assert isinstance(s.backend, JsonBackend)
    s2 = build_store_from_env({"SUPABASE_URL": "https://x.supabase.co/", "SUPABASE_KEY": "k"})
    assert isinstance(s2.backend, SupabaseBackend)
    assert s2.backend.url == "https://x.supabase.co"
