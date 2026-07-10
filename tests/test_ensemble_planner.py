"""Roadmap #9: ensemble tier — planner only, confirmation-gated, never
auto-executed."""
from aichaind.routing.ensemble import (DIFFICULTY_THRESHOLD, EnsemblePlan,
                                       plan_ensemble)
from aichaind.routing.pom_bridge import PomRouter
from tests.test_pom_integration import TABLE, TRUTH, _entry


def _chain(r, text):
    return r.route_ex(messages=[{"role": "user", "content": text}])


def test_easy_request_gets_no_ensemble():
    r = PomRouter(TABLE, TRUTH)
    chain, meta = _chain(r, "zdravo, kako si?")
    assert "ensemble_proposal" not in meta


def test_hard_verifiable_request_proposes_generate_verify():
    r = PomRouter(TABLE, TRUTH)
    # difficulty ~82: above the ensemble threshold but with an intelligence
    # floor (65.6) that still admits two catalog models
    hard = "Dokazi teoremu o konvergenciji reda i izvedi dokaz."
    chain, meta = _chain(r, hard)
    assert DIFFICULTY_THRESHOLD <= meta["difficulty"] < 88
    prop = meta.get("ensemble_proposal")
    assert prop and prop["pattern"] == "generate_verify"
    assert prop["requires_confirmation"] is True
    assert prop["est_total_cost_usd"] >= 0.0
    roles = [c["role"] for c in prop["calls"]]
    assert "candidate" in roles and "verifier" in roles
    # verifier must be a DIFFERENT model than the candidate
    models = {c["role"]: c["model"] for c in prop["calls"]}
    assert models["candidate"] != models["verifier"]


def test_locked_request_never_gets_ensemble():
    r = PomRouter(TABLE, TRUTH)
    hard = "Dokazi teoremu rigorozno i formalno. " * 30
    chain, meta = r.route_ex(messages=[{"role": "user", "content": hard}],
                             locked_model_id="acme/frontier")
    assert "ensemble_proposal" not in meta


def test_budget_headroom_blocks_expensive_plan():
    chain_stub = []
    plan = plan_ensemble(chain_stub, "coding", 95.0, budget_headroom=0.0)
    assert plan is None  # empty chain, and zero headroom regardless


def test_single_model_chain_cannot_ensemble():
    r = PomRouter({"routing_hierarchy": [
        _entry("solo/only", "solo", {"reasoning": 95, "general_chat": 90})]},
        {"version": 1, "profile": {"mode": "balanced"},
         "assets": {"api_keys": [{"provider": "solo", "key_ref": "env:S"}]}})
    hard = "Dokazi teoremu rigorozno i formalno, edge case-ovi. " * 20
    chain, meta = _chain(r, hard)
    assert "ensemble_proposal" not in meta


def test_cascade_exposes_proposal_on_decision():
    from aichaind.routing.cascade import CascadeRouter
    class Spy:
        enabled = True
        def route_ex(self, **kw):
            class P:  # minimal ScoredPath stand-in
                class path:
                    class model:
                        model_id = "beta/mid"
                        provider = "beta"
                    path_type = type("T", (), {"value": "free_quota"})()
                effective_cost = 0.0
                value_density = 1.0
            return [P()], {"ensemble_proposal": {"pattern": "best_of_n_judge",
                                                 "requires_confirmation": True}}
    cascade = CascadeRouter({"layer3_enabled": False})
    cascade.configure_pom(Spy())
    d = cascade.route(messages=[{"role": "user", "content": "hard task"}],
                      available_free_model="beta/mid")
    assert getattr(d, "ensemble_proposal", {}).get("pattern") == "best_of_n_judge"
