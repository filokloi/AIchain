#!/usr/bin/env python3
"""Tests for semantic AIchain routing controls."""

from aichaind.core.session import CanonicalSession
from aichaind.routing.cascade import CascadeRouter
from aichaind.routing.control_intent import _detect_control_language, parse_semantic_control
import aichaind.transport.http_server as http_server


def test_detect_control_language_defaults_to_english():
    normalized = "from now on use gpt 5 4 for this session"
    assert _detect_control_language("From now on use GPT-5.4 for this session.", normalized) == "en"


def test_detect_control_language_recognizes_serbian_signals():
    normalized = "od sada koristi gpt 5 4 za ovu sesiju"
    assert _detect_control_language("Od sada koristi GPT-5.4 za ovu sesiju.", normalized) == "sr"


def test_parse_semantic_manual_lock_for_gpt_54():
    intent = parse_semantic_control(
        [{"role": "user", "content": "Od sada koristi GPT-5.4 za ovu sesiju."}],
        roles={},
        provider_access_summary={
            "openai-codex": {"runtime_confirmed": True, "target_form_reached": True}
        },
    )

    assert intent is not None
    assert intent.mode == "manual"
    assert intent.model == "openai-codex/gpt-5.4"
    assert intent.provider == "openai-codex"
    assert intent.control_only is True
    assert intent.language == "sr"


def test_parse_semantic_manual_lock_for_gpt_54_when_codex_is_runtime_confirmed_only():
    intent = parse_semantic_control(
        [{"role": "user", "content": "Od sada koristi GPT-5.4 za ovu sesiju."}],
        roles={},
        provider_access_summary={
            "openai-codex": {"runtime_confirmed": True, "target_form_reached": False}
        },
    )

    assert intent is not None
    assert intent.mode == "manual"
    assert intent.model == "openai-codex/gpt-5.4"
    assert intent.provider == "openai-codex"


def test_parse_semantic_control_does_not_treat_filesystem_path_as_model():
    intent = parse_semantic_control(
        [{
            "role": "user",
            "content": "Use workspace file C:/Users/filok/.openclaw/workspace/HEARTBEAT.md and summarize it."
        }],
        roles={},
        provider_access_summary={
            "deepseek": {"runtime_confirmed": True},
            "openai-codex": {"runtime_confirmed": True, "target_form_reached": True},
        },
    )

    assert intent is None


def test_resolve_routing_control_persists_semantic_manual_lock():
    session = CanonicalSession(session_id="sem-1")
    http_server._roles = {}
    http_server._provider_access_layer = None

    control, error, changed = http_server._resolve_routing_control(
        session,
        {
            "messages": [
                {"role": "user", "content": "From now on use openai-codex/gpt-5.4 for this session."}
            ]
        },
    )

    assert error == ""
    assert changed is True
    assert control["manual_override_active"] is True
    assert control["locked_model"] == "openai-codex/gpt-5.4"
    assert control["locked_provider"] == "openai-codex"
    assert control["control_confirmation"].startswith("AIchain switched to manual lock")
    assert session.routing_mode == "manual"
    assert session.locked_model == "openai-codex/gpt-5.4"


def test_resolve_routing_control_supports_semantic_preference_with_followup_task():
    session = CanonicalSession(session_id="sem-2")
    control, error, changed = http_server._resolve_routing_control(
        session,
        {
            "messages": [
                {"role": "user", "content": "Prefer cheapest available and explain amortized analysis in 2 sentences."}
            ]
        },
    )

    assert error == ""
    assert changed is True
    assert control["routing_preference"] == "min_cost"
    assert control["control_only"] is False
    assert control["sanitized_messages"][-1]["content"] == "explain amortized analysis in 2 sentences"
    assert session.routing_preference == "min_cost"


def test_resolve_routing_control_supports_semantic_return_to_auto():
    session = CanonicalSession(
        session_id="sem-3",
        routing_mode="manual",
        routing_preference="max_intelligence",
        locked_model="openai-codex/gpt-5.4",
        locked_provider="openai-codex",
    )

    control, error, changed = http_server._resolve_routing_control(
        session,
        {"messages": [{"role": "user", "content": "Vrati automatsko biranje modela."}]},
    )

    assert error == ""
    assert changed is True
    assert control["manual_override_active"] is False
    assert control["routing_preference"] == "balanced"
    assert session.routing_mode == "auto"
    assert session.locked_model == ""
    assert session.locked_provider == ""
    assert session.routing_preference == "balanced"


def test_cascade_routing_preference_promotes_heavy_for_max_intelligence():
    router = CascadeRouter()
    applied = router._apply_routing_preference(
        "free",
        "max_intelligence",
        {"free": "deepseek/deepseek-chat", "heavy": "openai-codex/gpt-5.4", "local": "local/qwen"},
    )
    assert applied == "heavy"


def test_cascade_routing_preference_promotes_local_when_requested():
    router = CascadeRouter()
    applied = router._apply_routing_preference(
        "free",
        "prefer_local",
        {"free": "deepseek/deepseek-chat", "heavy": "openai-codex/gpt-5.4", "local": "lmstudio/qwen"},
    )
    assert applied == "local"
