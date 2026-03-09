#!/usr/bin/env python3
"""Tests for hardened request-path enforcement in aichaind.transport.http_server."""

import tempfile
from pathlib import Path

from aichaind.core.policy import PolicyEngine, PolicyResult
from aichaind.core.session import CanonicalSession, SessionStore
from aichaind.providers.base import CompletionResponse
from aichaind.core.summarizer import ContextSummarizer
from aichaind.routing.rules import RouteDecision
from aichaind.telemetry.route_eval import RouteEvalCollector
import aichaind.transport.http_server as http_server


class DummyController:
    state = {"system": "NORMAL", "circuit": "CLOSED"}


def test_merge_policy_results_combines_cloud_block_and_cost_limit():
    initial = PolicyResult(block_cloud=True, max_cost_per_turn=1.0, reason="pii")
    final = PolicyResult(max_cost_per_turn=0.5, reason="budget_warning")
    merged = http_server._merge_policy_results(initial, final)
    assert merged.block_cloud is True
    assert merged.max_cost_per_turn == 0.5
    assert merged.reason == "pii|budget_warning"


def test_final_policy_blocks_cloud_routes_when_policy_forbids_cloud():
    http_server._policy_engine = PolicyEngine({"pii_blocks_cloud": True})
    session = CanonicalSession(session_id="s1")
    initial = PolicyResult(block_cloud=True, reason="pii_detected_cloud_blocked")

    effective, reason = http_server._enforce_final_route_policy(
        session=session,
        initial_policy=initial,
        contains_pii=True,
        target_model="openai/gpt-4.1",
        target_provider="openai",
        estimated_cost_usd=0.02,
    )

    assert effective.block_cloud is True
    assert reason == "cloud_routing_blocked_by_policy"


def test_final_policy_allows_local_route_under_cloud_block():
    http_server._policy_engine = PolicyEngine({"pii_blocks_cloud": True})
    session = CanonicalSession(session_id="s2")
    initial = PolicyResult(block_cloud=True, reason="pii_detected_cloud_blocked")

    effective, reason = http_server._enforce_final_route_policy(
        session=session,
        initial_policy=initial,
        contains_pii=True,
        target_model="local/coder",
        target_provider="local",
        estimated_cost_usd=0.0,
    )

    assert effective.block_cloud is True
    assert reason == ""


def test_final_policy_blocks_when_estimated_cost_exceeds_budget_warning_limit():
    http_server._policy_engine = PolicyEngine({"max_cost_per_turn": 1.0})
    session = CanonicalSession(session_id="s3")
    session.budget_state.total_spent_usd = 8.5
    session.budget_state.session_limit_usd = 10.0

    effective, reason = http_server._enforce_final_route_policy(
        session=session,
        initial_policy=None,
        contains_pii=False,
        target_model="deepseek/deepseek-v3.1-terminus",
        target_provider="deepseek",
        estimated_cost_usd=0.8,
    )

    assert effective.max_cost_per_turn == 0.5
    assert reason == "per_turn_cost_exceeds_policy"


def test_restore_redactions_applies_to_content_and_raw_response():
    http_server._pii_redactor = http_server.PIIRedactor()
    response = CompletionResponse(
        model="openai/gpt-4.1",
        content="Hello [EMAIL_1]",
        raw_response={"choices": [{"message": {"content": "Hello [EMAIL_1]"}}]},
    )

    http_server._restore_redactions(response, {"[EMAIL_1]": "alice@example.com"})

    assert response.content == "Hello alice@example.com"
    assert response.raw_response["choices"][0]["message"]["content"] == "Hello alice@example.com"


def test_record_session_run_persists_provider_execution():
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(Path(tmp))
        http_server._session_store = store
        http_server._controller = DummyController()

        session = store.create()
        response = CompletionResponse(
            model="google/gemini-2.5-flash",
            content="ok",
            input_tokens=11,
            output_tokens=7,
            status="success",
        )

        http_server._record_session_run(
            session=session,
            model="google/gemini-2.5-flash",
            provider="google",
            response=response,
            exec_latency=123.4,
            estimated_cost_usd=0.0,
        )

        loaded = store.load(session.session_id)
        assert loaded is not None
        assert len(loaded.provider_runs) == 1
        assert loaded.provider_runs[0].provider == "google"
        assert loaded.provider_runs[0].latency_ms == 123.4


def test_maybe_compress_messages_updates_session_summary():
    http_server._summarizer = ContextSummarizer(max_turns=3, max_chars=80, target_turns=2)
    session = CanonicalSession(session_id="s4")
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "You must preserve UTC timestamps and the API contract."},
        {"role": "assistant", "content": "Conclusion: I will keep the API stable."},
        {"role": "user", "content": "Also keep rate limiting and auth token checks."},
        {"role": "assistant", "content": "Understood."},
    ]

    compressed, meta = http_server._maybe_compress_messages(session, messages)

    assert meta["compressed"] is True
    assert meta["summary_used"] is True
    assert len(compressed) < len(messages)
    assert session.summary_state.rolling_summary != ""
    assert session.summary_state.pinned_facts


def test_record_route_eval_appends_telemetry_ref_and_dataset():
    with tempfile.TemporaryDirectory() as tmp:
        collector = RouteEvalCollector(Path(tmp) / "route_eval.jsonl")
        http_server._route_eval_collector = collector
        session = CanonicalSession(session_id="s5")
        decision = RouteDecision(
            target_model="google/gemini-2.5-flash",
            confidence=0.91,
            decision_layers=["L1:heuristic", "L2:semantic:quick_general"],
            latency_ms=12.0,
            reason="heuristic_quick",
        )

        http_server._record_route_eval(
            session=session,
            messages=[{"role": "user", "content": "hello world"}],
            decision=decision,
            exec_status="success",
            exec_latency_ms=44.0,
            input_tokens=5,
            output_tokens=7,
            pii_detected=False,
            godmode=False,
        )

        records = collector.load_all()
        assert len(records) == 1
        assert records[0].final_model == "google/gemini-2.5-flash"
        assert session.telemetry_refs
        assert session.telemetry_refs[0].startswith("route_eval:")

def test_force_local_privacy_route_rewrites_cloud_route_when_local_brain_exists():
    http_server._roles = {"local_brain": "local/qwen2.5-coder"}
    decision = RouteDecision(
        target_model="openai/gpt-4.1",
        target_provider="openai",
        confidence=0.92,
        decision_layers=["L1:heuristic"],
        reason="heuristic_analyst",
    )
    initial = PolicyResult(block_cloud=True, reason="pii_detected_cloud_blocked")

    updated, model, provider, rerouted = http_server._maybe_force_local_privacy_route(
        decision=decision,
        initial_policy=initial,
        target_model="openai/gpt-4.1",
        target_provider="openai",
    )

    assert rerouted is True
    assert model == "local/qwen2.5-coder"
    assert provider == "local"
    assert updated.target_model == "local/qwen2.5-coder"
    assert updated.cost_tier == "local"
    assert "openai/gpt-4.1" in updated.fallback_chain
    assert "privacy_local_reroute" in updated.reason


def test_update_session_summary_state_tracks_commands_paths_and_model_ids():
    session = CanonicalSession(session_id="s6")
    messages = [
        {"role": "user", "content": "Run pytest tests/test_policy.py and inspect C:\\repo\\aichaind\\main.py using local/qwen2.5-coder."},
        {"role": "assistant", "content": "$ pytest tests/test_policy.py\nReview aichaind/transport/http_server.py next."},
    ]

    http_server._update_session_summary_state(session, messages)

    assert any("pytest tests/test_policy.py" in item for item in session.summary_state.commands)
    assert any("C:\\repo\\aichaind\\main.py" in item for item in session.summary_state.file_paths)
    assert any("aichaind/transport/http_server.py" in item for item in session.summary_state.file_paths)
    assert any("local/qwen2.5-coder" in item for item in session.summary_state.identifiers)


def test_maybe_compress_messages_injects_artifact_guardrail_for_missing_paths():
    http_server._summarizer = ContextSummarizer(max_turns=3, max_chars=80, target_turns=1)
    session = CanonicalSession(session_id="s7")
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Preserve exact file path C:\\repo\\src\\main.py and command pytest tests/test_http_server_enforcement.py."},
        {"role": "assistant", "content": "Conclusion: I will preserve those artifacts."},
        {"role": "user", "content": "Also keep context compression strict and safe with extra filler text to exceed the threshold quickly."},
        {"role": "assistant", "content": "Understood and acknowledged."},
    ]

    compressed, meta = http_server._maybe_compress_messages(session, messages)
    flattened = http_server._flatten_message_text(compressed)

    assert meta["compressed"] is True
    assert meta["artifact_guardrail_used"] is True
    assert meta["verification_passed"] is True
    assert "[Pinned Artifacts" in flattened
    assert "C:\\repo\\src\\main.py" in flattened

def test_update_session_summary_state_tracks_active_plan_and_open_loops():
    session = CanonicalSession(session_id="s8")
    messages = [
        {"role": "user", "content": "Plan:\n1. Validate policy path\n2. Reroute local-only sessions\nWhat remains open?"},
        {"role": "assistant", "content": "Next step: tighten output validation."},
    ]

    http_server._update_session_summary_state(session, messages)

    assert "1. Validate policy path" in session.summary_state.active_plan
    assert any("What remains open?" in item for item in session.summary_state.open_loops)
    assert any("Next step" in item for item in session.summary_state.open_loops)


def test_record_route_eval_appends_cache_ref():
    with tempfile.TemporaryDirectory() as tmp:
        collector = RouteEvalCollector(Path(tmp) / "route_eval.jsonl")
        http_server._route_eval_collector = collector
        session = CanonicalSession(session_id="s9")
        decision = RouteDecision(
            target_model="local/qwen2.5-coder",
            confidence=0.9,
            decision_layers=["L1:heuristic", "policy:privacy_local_reroute"],
            latency_ms=8.0,
            reason="privacy_local_reroute",
        )

        http_server._record_route_eval(
            session=session,
            messages=[{"role": "user", "content": "preserve C:\\repo\\main.py"}],
            decision=decision,
            exec_status="success",
            exec_latency_ms=22.0,
        )

        assert session.telemetry_refs
        assert session.cache_refs
        assert session.cache_refs[0].startswith("query:")
