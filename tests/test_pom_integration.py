"""Integration tests: user_truth loading + PomRouter bridge + cascade wiring.

Acceptance (PROJECT_STATE roadmap #2): routing logic calls build_chain(),
user_truth.json is loaded and schema-validated, sidecar answers through the
new pipeline, existing tests keep passing.
"""
import json

import pytest

from aichaind.pom import Boundary, PathType
from aichaind.routing.cascade import CascadeRouter
from aichaind.routing.pom_bridge import PomRouter, catalog_model_from_entry, map_task_type
from aichaind.routing.rules import RouteDecision
from aichaind.user_truth import (UserTruthError, boundary_from_truth,
                                 budget_from_truth, load_user_truth,
                                 profile_from_truth)


def _entry(model, provider, scores, prompt_cost=1e-7, completion_cost=4e-7,
           ctx=128_000, supported=None):
    return {
        "model": model, "provider": provider,
        "raw_metrics": {"prompt_cost": prompt_cost, "completion_cost": completion_cost,
                        "context_length": ctx},
        "task_metadata": {"quality_by_task": scores,
                          "supported": supported or list(scores)},
    }


TABLE = {"routing_hierarchy": [
    _entry("acme/frontier", "acme", {"coding": 92, "general_chat": 90, "reasoning": 91},
           prompt_cost=3e-6, completion_cost=1.5e-5,
           supported=["coding", "general_chat", "reasoning", "tool_agent_compatibility"]),
    _entry("beta/mid", "beta", {"coding": 74, "general_chat": 72, "reasoning": 70},
           prompt_cost=3e-7, completion_cost=1.2e-6),
    _entry("gamma/cheap", "gamma", {"coding": 50, "general_chat": 62, "reasoning": 48},
           prompt_cost=5e-8, completion_cost=2e-7),
]}

TRUTH = {
    "version": 1,
    "profile": {"mode": "balanced"},
    "budget": {"daily_limit": 5.0, "soft_threshold": 0.8, "hard_stop": True},
    "assets": {
        "api_keys": [{"provider": "acme", "key_ref": "env:ACME_KEY"}],
        "free_quotas": [{"provider": "beta", "quota_unit": "requests", "quota_per_day": 100}],
        "local_models": [{"endpoint": "http://localhost:11434/v1",
                          "model_id": "local/llama-8b"}],
        "subscriptions": [{"provider": "gamma", "plan": "pro", "monthly_price": 20,
                           "access_type": "app_only"}],
    },
    "privacy": {"default_boundary": "any",
                "rules": [{"match": {"keywords": ["poverljivo"]}, "boundary": "local_only"}]},
}


# ---------------------------------------------------------------- user_truth

def test_load_missing_file_returns_default(tmp_path):
    truth = load_user_truth(tmp_path / "nope.json")
    assert truth["version"] == 1
    assert truth["profile"]["mode"] == "balanced"


def test_load_valid_file(tmp_path):
    p = tmp_path / "user_truth.json"
    p.write_text(json.dumps(TRUTH), encoding="utf-8")
    truth = load_user_truth(p)
    assert truth["budget"]["daily_limit"] == 5.0


def test_load_invalid_file_raises(tmp_path):
    p = tmp_path / "user_truth.json"
    p.write_text(json.dumps({"profile": {"mode": "balanced"}}), encoding="utf-8")  # no version
    with pytest.raises(UserTruthError):
        load_user_truth(p)


def test_load_broken_json_raises(tmp_path):
    p = tmp_path / "user_truth.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(UserTruthError):
        load_user_truth(p)


def test_profile_modes():
    assert profile_from_truth({"profile": {"mode": "power"}}).intelligence == 90.0
    assert profile_from_truth({"profile": {"mode": "economy"}}).cost_sensitivity == 90.0
    custom = profile_from_truth({"profile": {"mode": "custom",
                                             "custom_weights": {"intelligence": 0.7, "cost": 0.2}}})
    assert custom.intelligence == 70.0 and custom.cost_sensitivity == 20.0


def test_budget_and_boundary():
    b = budget_from_truth(TRUTH, spent_today=1.0)
    assert b.daily_limit == 5.0 and b.headroom == 4.0
    assert boundary_from_truth(TRUTH, text="ovo je poverljivo pismo") == Boundary.LOCAL_ONLY
    assert boundary_from_truth(TRUTH, text="obicno pitanje") == Boundary.ANY


# ---------------------------------------------------------------- bridge

def test_catalog_model_conversion():
    cm = catalog_model_from_entry(TABLE["routing_hierarchy"][0])
    assert cm.model_id == "acme/frontier"
    assert cm.price_in == pytest.approx(3.0)      # $/M tokens
    assert cm.supports_tools is True
    assert cm.task_scores["coding"] == 92


def test_map_task_type():
    assert map_task_type("write python code") == "coding"
    assert map_task_type("", "visual") == "vision"
    assert map_task_type("hello") == "general_chat"


def test_paths_built_from_assets():
    r = PomRouter(TABLE, TRUTH)
    kinds = {(p.path_type, p.model.model_id) for p in r._paths}
    assert (PathType.PAY_AS_YOU_GO, "acme/frontier") in kinds
    assert (PathType.FREE_QUOTA, "beta/mid") in kinds
    assert any(pt == PathType.LOCAL for pt, _ in kinds)
    # app_only subscription must NOT become a programmatic path
    assert not any(pt == PathType.SUBSCRIPTION_API for pt, _ in kinds)
    assert r.enabled


def test_route_free_quota_wins_for_casual_chat():
    r = PomRouter(TABLE, TRUTH)
    chain = r.route(task_hint="just chatting", model_preference="free")
    assert chain and chain[0].path.path_type == PathType.FREE_QUOTA
    assert chain[0].effective_cost == 0.0


def test_route_privacy_forces_local():
    r = PomRouter(TABLE, TRUTH)
    chain = r.route(task_hint="chat",
                    messages=[{"role": "user", "content": "ovo je poverljivo"}])
    assert chain and all(s.path.path_type == PathType.LOCAL for s in chain)


def test_route_empty_without_assets():
    r = PomRouter(TABLE, {"version": 1, "profile": {"mode": "balanced"}})
    assert not r.enabled
    assert r.route(task_hint="chat") == []


# ---------------------------------------------------------------- cascade

def _route(cascade, text="write me some python code please, function to sort a list"):
    return cascade.route(messages=[{"role": "user", "content": text}],
                         available_free_model="beta/mid",
                         available_heavy_model="acme/frontier")


def test_cascade_uses_pom_chain():
    cascade = CascadeRouter({"layer3_enabled": False})
    cascade.configure_pom(PomRouter(TABLE, TRUTH))
    decision = _route(cascade)
    assert "L5:pom_value_density" in decision.decision_layers
    assert "pom_chain" in decision.reason
    assert decision.target_model in {"acme/frontier", "beta/mid", "local/llama-8b"}
    assert decision.fallback_chain  # ranked alternatives exposed


def test_cascade_without_pom_still_routes():
    cascade = CascadeRouter({"layer3_enabled": False})
    decision = _route(cascade)
    assert decision.target_model
    assert "L5:pom_value_density" not in decision.decision_layers


def test_cascade_pom_error_falls_through():
    class Broken:
        enabled = True
        def route(self, **kw):
            raise RuntimeError("boom")
    cascade = CascadeRouter({"layer3_enabled": False})
    cascade.configure_pom(Broken())
    decision = _route(cascade)
    assert decision.target_model  # legacy path still answered
    assert "L5:pom_value_density" not in decision.decision_layers


# ---------------------------------------------------------------- phase 8b:
# session-aware stickiness, cache economics, quota pacing (roadmap #5)

from aichaind.pom import Boundary as _B  # noqa: E402
from aichaind.routing.pom_bridge import (DEFAULT_CACHE_FACTOR,  # noqa: E402
                                         PROVIDER_CACHE_FACTORS,
                                         catalog_model_from_entry as _cme)


def test_provider_cache_factors():
    ds = _cme(_entry("deepseek/deepseek-v4-flash", "Deepseek", {"coding": 90}))
    assert ds.cached_rate_factor == PROVIDER_CACHE_FACTORS["deepseek"] == 0.02
    an = _cme(_entry("anthropic/claude-sonnet-4.6", "Anthropic", {"coding": 95}))
    assert an.cached_rate_factor == 0.1
    unknown = _cme(_entry("acme/frontier", "acme", {"coding": 92}))
    assert unknown.cached_rate_factor == DEFAULT_CACHE_FACTOR == 1.0


def test_sticky_hysteresis_keeps_session_model():
    """A near-tie challenger must NOT unseat the session's model — switching
    would discard the provider-side prefix cache for a ~2% quality gain."""
    near_tie = {"routing_hierarchy": [
        _entry("alpha/a", "alpha", {"general_chat": 91}),
        _entry("omega/b", "omega", {"general_chat": 90}),
    ]}
    truth = {"version": 1, "profile": {"mode": "balanced"},
             "assets": {"free_quotas": [{"provider": "alpha", "quota_per_day": 50},
                                        {"provider": "omega", "quota_per_day": 50}]}}
    r = PomRouter(near_tie, truth)
    fresh = r.route(messages=[{"role": "user", "content": "cao, jos si tu?"}])
    assert fresh[0].path.model.model_id == "alpha/a"  # better model wins cold
    sticky = r.route(messages=[{"role": "user", "content": "cao, jos si tu?"}],
                     sticky_model_id="omega/b", transcript_tokens=40_000)
    assert sticky[0].path.model.model_id == "omega/b", \
        "2% quality gap must not beat 19-point switch threshold"


def test_long_transcript_excludes_small_context_models():
    """Context hard filter: a 200k-token conversation cannot route to a
    128k-window model at all."""
    r = PomRouter(TABLE, TRUTH)
    chain = r.route(task_hint="chatting", transcript_tokens=200_000)
    assert chain == []


def test_locked_model_via_session_context():
    r = PomRouter(TABLE, TRUTH)
    chain = r.route(task_hint="chat", locked_model_id="acme/frontier")
    assert chain and all(s.path.model.model_id == "acme/frontier" for s in chain)


def test_quota_pacing_reserves_bottom_for_hard_questions():
    truth = dict(TRUTH)
    truth["assets"] = {"free_quotas": [{"provider": "beta", "quota_per_day": 10}]}
    r = PomRouter(TABLE, truth)
    # burn quota down into the reserve zone
    for _ in range(9):
        r.record_usage("beta/mid", PathType.FREE_QUOTA)
    easy = r.route(messages=[{"role": "user", "content": "zdravo"}])
    assert not any(s.path.path_type == PathType.FREE_QUOTA for s in easy), \
        "easy request must not burn the reserved quota tail"
    hard = r.route(messages=[{"role": "user", "content":
                              "Dokazi teoremu i izvedi formalni dokaz indukcijom"}])
    assert any(s.path.path_type == PathType.FREE_QUOTA for s in hard), \
        "hard request may use the reserve"


def test_record_usage_decrements_quota():
    truth = dict(TRUTH)
    truth["assets"] = {"free_quotas": [{"provider": "beta", "quota_per_day": 2}]}
    r = PomRouter(TABLE, truth)
    r.record_usage("beta/mid", PathType.FREE_QUOTA)
    r.record_usage("beta/mid", PathType.FREE_QUOTA)
    chain = r.route(messages=[{"role": "user", "content":
                               "Dokazi teoremu i izvedi formalni dokaz"}])
    assert not any(s.path.path_type == PathType.FREE_QUOTA and s.effective_cost == 0.0
                   for s in chain), "exhausted quota must not appear as free"


def test_cascade_passes_session_memory_to_pom():
    captured = {}
    class Spy:
        enabled = True
        def route(self, **kw):
            captured.update(kw)
            return []
    cascade = CascadeRouter({"layer3_enabled": False})
    cascade.configure_pom(Spy())
    cascade.route(messages=[{"role": "user", "content": "nastavi gde smo stali sa refaktorisanjem koda"}],
                  available_free_model="beta/mid",
                  session_context={"sticky_model_id": "beta/mid",
                                   "transcript_tokens": 42_000,
                                   "locked_model_id": None})
    assert captured.get("sticky_model_id") == "beta/mid"
    assert captured.get("transcript_tokens") == 42_000


def test_build_session_context_from_canonical_session():
    from aichaind.core.session import CanonicalSession, ProviderRun
    from aichaind.transport.http_server import _build_session_context
    s = CanonicalSession(session_id="t1")
    s.record_run(ProviderRun(model="beta/mid", status="success",
                             input_tokens=1000, output_tokens=500, cost_usd=0.01))
    s.record_run(ProviderRun(model="acme/frontier", status="error",
                             input_tokens=200, output_tokens=0))
    ctx = _build_session_context(s)
    assert ctx["sticky_model_id"] == "beta/mid"      # last SUCCESSFUL run
    assert ctx["transcript_tokens"] == 1700
    assert ctx["spent_today_usd"] == 0.01
    assert _build_session_context(None) == {}
