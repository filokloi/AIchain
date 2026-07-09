"""Tests for the POM / value-density routing core. Run: pytest test_pom.py -q"""
from aichaind.pom import (AccessPath, Boundary, Budget, CatalogModel, PathType, Profile,
                 Request, build_chain, effective_cost, score_path,
                 switch_threshold)

FRONTIER = CatalogModel("frontier-x", "acme", {"coding": 90, "chat_casual": 88},
                        price_in=3.0, price_out=15.0, context_window=200_000,
                        supports_tools=True, supports_vision=True)
MID = CatalogModel("mid-y", "beta", {"coding": 72, "chat_casual": 70},
                   price_in=0.3, price_out=1.2, context_window=128_000,
                   supports_tools=True)
LOCAL8B = CatalogModel("local-8b", "self", {"coding": 45, "chat_casual": 60},
                       price_in=0.0, price_out=0.0, context_window=32_000)

P_PAID = AccessPath(FRONTIER, PathType.PAY_AS_YOU_GO)
P_FREE = AccessPath(MID, PathType.FREE_QUOTA, quota_remaining=100)
P_LOCAL = AccessPath(LOCAL8B, PathType.LOCAL, endpoint="http://localhost:11434/v1")


def req(**kw):
    base = dict(task_type="coding", difficulty=50, est_input_tokens=2_000,
                est_output_tokens=1_000)
    base.update(kw)
    return Request(**base)


def test_free_and_local_paths_cost_zero():
    assert effective_cost(P_FREE, req()) == 0.0
    assert effective_cost(P_LOCAL, req()) == 0.0
    assert effective_cost(P_PAID, req()) > 0.0


def test_exhausted_quota_is_unreachable():
    empty = AccessPath(MID, PathType.FREE_QUOTA, quota_remaining=0)
    assert effective_cost(empty, req()) == float("inf")


def test_sticky_cache_discount():
    r = req(transcript_tokens=100_000)
    cold = effective_cost(P_PAID, r, sticky=False)
    warm = effective_cost(P_PAID, r, sticky=True)
    assert warm < cold  # cached prefix must be cheaper than re-billing


def test_economy_free_beats_paid_frontier():
    prof = Profile(intelligence=40, cost_sensitivity=90)
    chain = build_chain([P_PAID, P_FREE, P_LOCAL], req(), prof, Budget())
    assert chain[0].path.model.model_id == "mid-y"  # free + good enough wins


def test_power_frontier_wins():
    prof = Profile(intelligence=95, cost_sensitivity=10)
    chain = build_chain([P_PAID, P_FREE], req(difficulty=85), prof, Budget())
    assert chain[0].path.model.model_id == "frontier-x"


def test_difficulty_raises_floor_dropping_weak_models():
    prof = Profile(intelligence=40, cost_sensitivity=90, min_intelligence=0)
    s = score_path(P_LOCAL, req(difficulty=80), prof, Budget())  # floor = 64 > 45
    assert s.excluded_reason and s.excluded_reason.startswith("floor")


def test_privacy_local_only_is_absolute():
    prof = Profile(intelligence=95, cost_sensitivity=0)
    chain = build_chain([P_PAID, P_FREE, P_LOCAL],
                        req(difficulty=10, boundary=Boundary.LOCAL_ONLY),
                        prof, Budget())
    assert [s.path.model.model_id for s in chain] == ["local-8b"]


def test_capability_guard_tools():
    s = score_path(P_LOCAL, req(needs_tools=True), Profile(), Budget())
    assert s.excluded_reason == "capability:tools"


def test_budget_hard_stop_leaves_only_free():
    b = Budget(daily_limit=0.05, spent_today=0.05, hard_stop=True)
    chain = build_chain([P_PAID, P_FREE], req(), Profile(50, 50), b)
    assert all(s.effective_cost == 0.0 for s in chain)
    assert chain  # free path survives


def test_lock_ignores_economy_but_not_privacy():
    prof = Profile(intelligence=0, cost_sensitivity=100)  # extreme economy
    chain = build_chain([P_PAID, P_FREE, P_LOCAL],
                        req(locked_model_id="frontier-x"), prof,
                        Budget(daily_limit=0.001, spent_today=0.0))
    assert [s.path.model.model_id for s in chain] == ["frontier-x"]
    # ...but a privacy boundary still wins over the lock:
    chain2 = build_chain([P_PAID, P_FREE, P_LOCAL],
                         req(locked_model_id="frontier-x",
                             boundary=Boundary.LOCAL_ONLY), prof, Budget())
    assert chain2 == []


def test_sticky_hysteresis_keeps_incumbent_on_small_gap():
    prof = Profile(intelligence=60, cost_sensitivity=50)
    r = req(sticky_model_id="mid-y", transcript_tokens=50_000)
    chain = build_chain([P_PAID, P_FREE], r, prof, Budget())
    assert chain[0].path.model.model_id == "mid-y"  # promoted despite ranking


def test_switch_threshold_grows_with_transcript():
    assert switch_threshold(0) < switch_threshold(200_000)


def test_expiring_credit_gets_priority():
    dying = AccessPath(MID, PathType.PREPAID_CREDIT, credit_remaining=5.0,
                       credit_days_to_expiry=1)
    durable = AccessPath(MID, PathType.PAY_AS_YOU_GO)
    prof = Profile(intelligence=50, cost_sensitivity=50)
    s_dying = score_path(dying, req(), prof, Budget())
    s_durable = score_path(durable, req(), prof, Budget())
    assert s_dying.value_density > s_durable.value_density
