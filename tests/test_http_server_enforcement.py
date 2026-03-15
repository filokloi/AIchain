#!/usr/bin/env python3
"""Tests for hardened request-path enforcement in aichaind.transport.http_server."""

import tempfile
from pathlib import Path
from types import SimpleNamespace

from aichaind.core.policy import PolicyEngine, PolicyResult
from aichaind.core.session import CanonicalSession, SessionStore
from aichaind.providers.access import ProviderAccessDecision
from aichaind.providers.base import CompletionResponse
from aichaind.core.summarizer import ContextSummarizer
from aichaind.routing.rules import RouteDecision
from aichaind.routing.cost_optimizer import CostRoute
from aichaind.telemetry.route_eval import RouteEvalCollector
from aichaind.providers.local_profile import LocalModelProfile, LocalProfileStore
import aichaind.transport.http_server as http_server


class DummyController:
    state = {"system": "NORMAL", "circuit": "CLOSED"}


class _DummyHandler:
    def __init__(self, client_ip: str, authorization: str = ""):
        self.client_address = (client_ip, 0)
        self.headers = {}
        if authorization:
            self.headers["Authorization"] = authorization


def test_merge_policy_results_combines_cloud_block_local_preference_and_cost_limit():
    initial = PolicyResult(block_cloud=True, prefer_local=True, max_cost_per_turn=1.0, reason="pii")
    final = PolicyResult(max_cost_per_turn=0.5, reason="budget_warning")
    merged = http_server._merge_policy_results(initial, final)
    assert merged.block_cloud is True
    assert merged.prefer_local is True
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


def test_provider_timeout_ms_scales_up_for_openai_codex_coding_tasks():
    payload = {
        "messages": [{"role": "user", "content": "Write only Python code for a function add(a, b) with a unit test."}],
        "max_tokens": 120,
    }

    timeout_ms = http_server._provider_timeout_ms("openai-codex", payload)

    assert timeout_ms >= 90000


def test_provider_timeout_ms_stays_lower_for_openai_codex_simple_chat():
    payload = {
        "messages": [{"role": "user", "content": "Say exactly CLOUD_OK and nothing else."}],
        "max_tokens": 20,
    }

    timeout_ms = http_server._provider_timeout_ms("openai-codex", payload)

    assert 45000 <= timeout_ms <= 75000


def test_provider_timeout_ms_scales_up_for_openai_codex_security_sensitive_tasks():
    payload = {
        "messages": [{"role": "user", "content": "Use password MyPassword123! to log into example.com and then reply exactly LOGIN_PATH_OK."}],
        "max_tokens": 24,
    }

    timeout_ms = http_server._provider_timeout_ms("openai-codex", payload)

    assert timeout_ms >= 80000


class _ResolvedAccessLayer:
    def resolve(self, provider_name):
        class Decision:
            selected_method = "api_key"
            status = "runtime_confirmed"
        return Decision()


def test_refresh_access_decision_for_provider_uses_final_provider():
    http_server._provider_access_layer = _ResolvedAccessLayer()
    original = type("Decision", (), {"selected_method": "oauth", "status": "runtime_confirmed"})()

    refreshed = http_server._refresh_access_decision_for_provider("deepseek", original)

    assert refreshed.selected_method == "api_key"


def test_build_success_response_payload_normalizes_openclaw_compat_shape():
    session = CanonicalSession(session_id="compat-session")
    response = CompletionResponse(
        model="deepseek/deepseek-chat",
        content="GUI_CHAT_OK",
        input_tokens=11,
        output_tokens=4,
        finish_reason="stop",
        raw_response={"id": "provider-native-id", "choices": [{"message": {"content": ""}}]},
    )
    decision = RouteDecision(
        target_model="deepseek/deepseek-chat",
        target_provider="deepseek",
        confidence=0.91,
        decision_layers=["L1:heuristic", "L2:semantic:quick_general"],
        latency_ms=12.0,
        reason="heuristic_quick",
    )

    payload = http_server._build_success_response_payload(
        requested_model="aichain/dual-brain",
        target_model="deepseek/deepseek-chat",
        session=session,
        response=response,
        decision=decision,
        target_provider="deepseek",
        exec_latency=33.4,
        contains_pii=False,
        pii_redacted=False,
        balance_report=None,
        failover_used=False,
        access_failover_used=False,
        access_decision=type("Decision", (), {"selected_method": "api_key", "status": "runtime_confirmed"})(),
        local_reroute_used=False,
        codex_bridge_used=False,
        compression_meta={"compressed": False},
        routing_control_meta={"routing_mode": "auto"},
    )

    assert payload["id"] == "provider-native-id"
    assert payload["object"] == "chat.completion"
    assert payload["model"] == "aichain/dual-brain"
    assert payload["choices"][0]["message"]["role"] == "assistant"
    assert payload["choices"][0]["message"]["content"] == "GUI_CHAT_OK"
    assert payload["_aichaind"]["routed_model"] == "deepseek/deepseek-chat"
    assert payload["_aichaind"]["provider_model"] == "deepseek/deepseek-chat"


def test_build_success_response_payload_can_omit_aichain_metadata_for_openclaw_compat():
    session = CanonicalSession(session_id="compat-minimal")
    response = CompletionResponse(
        model="deepseek/deepseek-chat",
        content="GUI_CHAT_OK",
        input_tokens=11,
        output_tokens=4,
        finish_reason="stop",
    )
    decision = RouteDecision(
        target_model="deepseek/deepseek-chat",
        target_provider="deepseek",
        confidence=0.91,
        decision_layers=["L1:coding_intent"],
        latency_ms=12.0,
        reason="heuristic_code_generation",
    )

    payload = http_server._build_success_response_payload(
        requested_model="aichain/dual-brain",
        target_model="deepseek/deepseek-chat",
        session=session,
        response=response,
        decision=decision,
        target_provider="deepseek",
        exec_latency=33.4,
        contains_pii=False,
        pii_redacted=False,
        balance_report=None,
        failover_used=False,
        access_failover_used=False,
        access_decision=None,
        local_reroute_used=False,
        codex_bridge_used=False,
        compression_meta={"compressed": False},
        routing_control_meta={"routing_mode": "auto"},
        compat_openclaw_bridge=True,
    )

    assert payload["model"] == "aichain/dual-brain"
    assert payload["choices"][0]["message"]["content"] == "GUI_CHAT_OK"
    assert "_aichaind" not in payload


def test_build_openai_stream_frames_emits_assistant_delta_content_and_done():
    payload = {
        "id": "chatcmpl_stream_test",
        "created": 123,
        "model": "aichain/dual-brain",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "STREAM_OK",
            },
            "finish_reason": "stop",
        }],
    }

    frames = http_server._build_openai_stream_frames(payload)

    assert frames[0].startswith("data: ")
    assert '"delta": {"role": "assistant"}' in frames[0]
    assert any('"content": "STREAM_OK"' in frame for frame in frames)
    assert '"finish_reason": "stop"' in frames[-2]
    assert frames[-1] == "data: [DONE]\n\n"


def test_maybe_route_openai_codex_oauth_uses_access_metadata_without_full_discovery(monkeypatch):
    class _AccessLayer:
        def resolve(self, provider_name):
            if provider_name != "openai-codex":
                raise AssertionError("unexpected provider lookup")
            return SimpleNamespace(
                selected_method="oauth",
                runtime_confirmed=True,
                verified_models=["openai-codex/gpt-5.4"],
                preferred_model="openai-codex/gpt-5.4",
                target_model="openai-codex/gpt-5.4",
            )

    class _CodexAdapter:
        def discover(self):
            raise AssertionError("full discover should not be called")

        def resolve_preferred_model(self, requested_model="", available_models=None):
            assert available_models == ["openai-codex/gpt-5.4"]
            return "openai-codex/gpt-5.4"

    http_server._provider_access_layer = _AccessLayer()
    monkeypatch.setattr(http_server, "get_adapter", lambda provider_name: _CodexAdapter() if provider_name == "openai-codex" else None)

    decision = RouteDecision(
        target_model="openai/gpt-5.4",
        target_provider="openai",
        confidence=0.91,
        decision_layers=["L1:semantic:coding"],
        latency_ms=10.0,
        reason="coding_intent",
    )

    updated, routed_model, routed_provider, bridged = http_server._maybe_route_openai_codex_oauth(
        decision,
        "openai/gpt-5.4",
        "openai",
    )

    assert bridged is True
    assert routed_model == "openai-codex/gpt-5.4"
    assert routed_provider == "openai-codex"
    assert updated.target_model == "openai-codex/gpt-5.4"
    assert updated.target_provider == "openai-codex"


def test_pii_metadata_can_distinguish_detected_from_redacted():
    http_server._pii_redactor = http_server.PIIRedactor()
    http_server._input_redaction_enabled = False

    messages = [{"role": "user", "content": "My SSN is 123-45-6789"}]
    redaction_map = {}
    pii_categories = http_server.scan_messages(messages, http_server._pii_redactor)

    assert pii_categories == ["ssn"]
    assert redaction_map == {}


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

def test_force_local_privacy_route_prefers_local_without_strict_cloud_block():
    http_server._roles = {"local_brain": "local/qwen2.5-coder"}
    decision = RouteDecision(
        target_model="deepseek/deepseek-chat",
        target_provider="deepseek",
        confidence=0.85,
        decision_layers=["L1:heuristic"],
        reason="heuristic_quick_general",
    )
    initial = PolicyResult(prefer_local=True, reason="pii_detected_local_preferred")

    updated, model, provider, rerouted = http_server._maybe_force_local_privacy_route(
        decision=decision,
        initial_policy=initial,
        target_model="deepseek/deepseek-chat",
        target_provider="deepseek",
    )

    assert rerouted is True
    assert model == "local/qwen2.5-coder"
    assert provider == "local"
    assert updated.target_model == "local/qwen2.5-coder"


def test_force_local_privacy_route_keeps_cloud_when_no_local_and_not_strict():
    http_server._roles = {"local_brain": ""}
    decision = RouteDecision(
        target_model="deepseek/deepseek-chat",
        target_provider="deepseek",
        confidence=0.85,
        decision_layers=["L1:heuristic"],
        reason="heuristic_quick_general",
    )
    initial = PolicyResult(prefer_local=True, reason="pii_detected_local_preferred")

    updated, model, provider, rerouted = http_server._maybe_force_local_privacy_route(
        decision=decision,
        initial_policy=initial,
        target_model="deepseek/deepseek-chat",
        target_provider="deepseek",
    )

    assert rerouted is False
    assert model == "deepseek/deepseek-chat"
    assert provider == "deepseek"
    assert updated.target_model == "deepseek/deepseek-chat"


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


def test_build_request_includes_local_profile_timeout_override(tmp_path):
    profile_store = LocalProfileStore(tmp_path / "local_profiles.json")
    profile_store.upsert(LocalModelProfile(
        provider="lmstudio",
        model="lmstudio/qwen/qwen3-4b-thinking-2507",
        base_url="http://127.0.0.1:1234/v1",
        profiled_at="2026-03-11T12:00:00Z",
        runtime_confirmed=True,
        success_rate=1.0,
        average_latency_ms=1200.0,
        average_ttft_ms=450.0,
        average_tokens_per_second=18.5,
        speed_score=88.0,
        stability_score=100.0,
        safe_timeout_ms=87000,
        prompt_type_suitability={"coding": 100.0},
    ))
    http_server._local_profile_store = profile_store
    adapter = http_server.get_adapter("lmstudio")

    request = http_server._build_request(
        payload={"max_tokens": 120, "temperature": 0.2, "stream": False},
        messages=[{"role": "user", "content": "Write Python code"}],
        target_model="lmstudio/qwen/qwen3-4b-thinking-2507",
        adapter=adapter,
    )

    assert request.extra["timeout_ms"] == 87000


def test_build_request_defaults_to_concise_budget_for_simple_chat_when_max_tokens_missing():
    adapter = SimpleNamespace(name="deepseek", format_model_id=lambda model_id: model_id)

    request = http_server._build_request(
        payload={"temperature": 0.1, "stream": False},
        messages=[{"role": "user", "content": "What is DNS?"}],
        target_model="deepseek/deepseek-chat",
        adapter=adapter,
    )

    assert request.max_tokens == 64


def test_build_request_defaults_to_wider_budget_for_coding_when_max_tokens_missing():
    adapter = SimpleNamespace(name="openai-codex", format_model_id=lambda model_id: model_id)

    request = http_server._build_request(
        payload={"stream": False},
        messages=[{"role": "user", "content": "Write Python code for a small snake game."}],
        target_model="openai-codex/gpt-5.4",
        adapter=adapter,
    )

    assert request.max_tokens == 900


def test_build_request_respects_explicit_max_tokens():
    adapter = SimpleNamespace(name="deepseek", format_model_id=lambda model_id: model_id)

    request = http_server._build_request(
        payload={"max_tokens": 640, "stream": False},
        messages=[{"role": "user", "content": "What is DNS?"}],
        target_model="deepseek/deepseek-chat",
        adapter=adapter,
    )

    assert request.max_tokens == 640


def test_ensure_provider_access_fails_over_when_local_runtime_is_unhealthy(monkeypatch):
    class _AccessLayer:
        def __init__(self):
            self.decisions = {
                "lmstudio": ProviderAccessDecision(
                    provider="lmstudio",
                    selected_method="local",
                    status="runtime_confirmed",
                    reason="discover:authenticated:models=1",
                    runtime_confirmed=True,
                    target_form_reached=True,
                ),
                "deepseek": ProviderAccessDecision(
                    provider="deepseek",
                    selected_method="api_key",
                    status="runtime_confirmed",
                    reason="discover:authenticated:models=2",
                    runtime_confirmed=True,
                    target_form_reached=True,
                ),
            }

        def resolve(self, provider_name):
            return self.decisions[provider_name]

        def mark_runtime_result(self, provider, confirmed, reason="", target_form_reached=None, **kwargs):
            decision = self.decisions[provider]
            decision.runtime_confirmed = bool(confirmed)
            decision.target_form_reached = bool(confirmed if target_form_reached is None else target_form_reached)
            decision.status = "runtime_confirmed" if confirmed else "target_form_not_reached"
            if reason:
                decision.reason = reason

    http_server._provider_access_layer = _AccessLayer()
    http_server._LOCAL_RUNTIME_HEALTH_CACHE.clear()
    http_server._cascade_router = SimpleNamespace(
        _cost_optimizer=SimpleNamespace(
            optimize=lambda **kwargs: CostRoute(
                model="deepseek/deepseek-chat",
                provider="deepseek",
                estimated_cost_usd=0.001,
                reason="verified_direct_fallback:deepseek",
                tier="free",
            )
        )
    )
    monkeypatch.setattr(http_server, "_local_runtime_ready", lambda provider_name, adapter: (False, "runtime_health_check_failed"))

    decision = RouteDecision(
        target_model="lmstudio/qwen/qwen3-4b-thinking-2507",
        target_provider="lmstudio",
        confidence=0.91,
        decision_layers=["L2:semantic:code_generation"],
        reason="semantic_code_generation",
    )
    balance_report = SimpleNamespace(
        total_available_usd=5.0,
        providers_with_credits=["deepseek"],
        balances={"deepseek": SimpleNamespace(has_credits=True)},
    )

    decision, target_model, target_provider, access_decision, failover_used, block_reason = http_server._ensure_provider_access(
        decision=decision,
        payload={"messages": [{"role": "user", "content": "Write code"}]},
        target_model="lmstudio/qwen/qwen3-4b-thinking-2507",
        target_provider="lmstudio",
        balance_report=balance_report,
        allow_failover=True,
    )

    assert failover_used is True
    assert block_reason == ""
    assert target_provider == "deepseek"
    assert target_model == "deepseek/deepseek-chat"
    assert access_decision.selected_method == "api_key"


def test_do_post_returns_json_500_on_unhandled_exception(monkeypatch):
    captured = {}
    handler = object.__new__(http_server.AichainDHandler)
    handler.path = '/v1/chat/completions'
    handler._handle_chat = lambda: (_ for _ in ()).throw(RuntimeError('boom'))
    handler._send_json = lambda status, data: captured.update({'status': status, 'data': data})
    handler.send_error = lambda status, message: captured.update({'status': status, 'data': {'error': message}})
    handler.close_connection = False

    http_server.AichainDHandler.do_POST(handler)

    assert captured['status'] == 500
    assert captured['data']['error'] == 'Internal server error: RuntimeError'


def test_trusted_openclaw_provider_bridge_accepts_loopback_bearer_ignore():
    handler = _DummyHandler("127.0.0.1", "Bearer ignore")

    assert http_server._is_trusted_openclaw_provider_bridge(handler) is True


def test_trusted_openclaw_provider_bridge_rejects_non_loopback_or_other_tokens():
    remote = _DummyHandler("10.0.0.5", "Bearer ignore")
    wrong_token = _DummyHandler("127.0.0.1", "Bearer abc123")

    assert http_server._is_trusted_openclaw_provider_bridge(remote) is False
    assert http_server._is_trusted_openclaw_provider_bridge(wrong_token) is False

def test_start_server_uses_threading_http_server(monkeypatch):
    captured = {}

    class FakeServer:
        daemon_threads = True
        allow_reuse_address = True

        def __init__(self, address, handler):
            captured['address'] = address
            captured['handler'] = handler

    monkeypatch.setattr(http_server, 'AichainThreadingHTTPServer', FakeServer)

    server = http_server.start_server(port=9999)

    assert isinstance(server, FakeServer)
    assert captured['address'] == ('127.0.0.1', 9999)
    assert captured['handler'] is http_server.AichainDHandler
