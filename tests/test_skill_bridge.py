#!/usr/bin/env python3
"""Tests for the thin OpenClaw skill bridge."""

from types import SimpleNamespace
import importlib.util
from pathlib import Path


_skill_path = Path(r"C:/Users/filok/OneDrive/Desktop/AI chain for Open Claw envirement/openclaw-skill/skill.py")
_spec = importlib.util.spec_from_file_location("openclaw_skill_bridge", _skill_path)
skill = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(skill)


def test_configure_stdio_reconfigures_streams(monkeypatch):
    calls = []

    class _Stream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(skill.sys, 'stdout', _Stream())
    monkeypatch.setattr(skill.sys, 'stderr', _Stream())

    skill.configure_stdio()

    assert calls == [
        {'encoding': 'utf-8', 'errors': 'replace'},
        {'encoding': 'utf-8', 'errors': 'replace'},
    ]


def test_forward_to_sidecar_handles_json_response(monkeypatch):
    monkeypatch.setattr(skill, 'read_auth_token', lambda: 'token')

    class _Resp:
        status_code = 200
        headers = {'content-type': 'application/json'}
        def json(self):
            return {'ok': True}

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith('/v1/chat/completions')
        assert headers['X-AIchain-Token'] == 'token'
        return _Resp()

    monkeypatch.setattr(skill.requests, 'post', fake_post)

    result = skill.forward_to_sidecar('/v1/chat/completions', {'messages': []})

    assert result == {'status': 200, 'body': {'ok': True}}


def test_build_chat_payload_supports_manual_override():
    args = SimpleNamespace(
        message='hello',
        max_tokens=123,
        temperature=0.3,
        session_id='sess-1',
        manual=True,
        auto=False,
        manual_model='openai-codex/gpt-5.4',
        manual_provider='openai-codex',
        persist=True,
    )

    payload = skill.build_chat_payload(args)

    assert payload['session_id'] == 'sess-1'
    assert payload['_aichain_control'] == {
        'mode': 'manual',
        'model': 'openai-codex/gpt-5.4',
        'provider': 'openai-codex',
        'persist_for_session': True,
    }


def test_build_chat_payload_supports_return_to_auto():
    args = SimpleNamespace(
        message='hello',
        max_tokens=123,
        temperature=0.3,
        session_id='sess-2',
        manual=False,
        auto=True,
        manual_model='',
        manual_provider='',
        persist=True,
    )

    payload = skill.build_chat_payload(args)

    assert payload['_aichain_control'] == {
        'mode': 'auto',
        'persist_for_session': True,
    }


def test_cmd_chat_forwards_manual_control(monkeypatch, capsys):
    captured = {}

    def fake_forward(endpoint, payload, sidecar_url=skill.DEFAULT_SIDECAR_URL):
        captured['endpoint'] = endpoint
        captured['payload'] = payload
        return {
            'status': 200,
            'body': {'choices': [{'message': {'content': 'LOCK_OK'}}]},
        }

    monkeypatch.setattr(skill, 'forward_to_sidecar', fake_forward)

    args = SimpleNamespace(
        message='Lock this model',
        max_tokens=321,
        temperature=0.1,
        session_id='sess-3',
        manual=True,
        auto=False,
        manual_model='openai-codex/gpt-5.4',
        manual_provider='openai-codex',
        persist=False,
    )

    skill.cmd_chat(args)
    out = capsys.readouterr().out.strip()

    assert out == 'LOCK_OK'
    assert captured['endpoint'] == '/v1/chat/completions'
    assert captured['payload']['_aichain_control']['mode'] == 'manual'
    assert captured['payload']['_aichain_control']['model'] == 'openai-codex/gpt-5.4'


def test_cmd_chat_surfaces_clean_route_unavailable_message(monkeypatch, capsys):
    monkeypatch.setattr(skill, 'forward_to_sidecar', lambda *args, **kwargs: {
        'status': 503,
        'body': {'error': 'provider_access_unavailable:openai-codex:runtime_probe_timeout'},
    })

    args = SimpleNamespace(
        message='Use premium model',
        max_tokens=32,
        temperature=0.2,
        session_id='sess-premium',
        manual=True,
        auto=False,
        manual_model='openai-codex/gpt-5.4',
        manual_provider='openai-codex',
        persist=False,
    )

    try:
        skill.cmd_chat(args)
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out.strip()

    assert out == '⚠️ The requested AI route is currently unavailable. Switch back to auto mode or choose another model.'


def test_cmd_chat_keeps_daemon_offline_message_for_generic_503(monkeypatch, capsys):
    monkeypatch.setattr(skill, 'forward_to_sidecar', lambda *args, **kwargs: {
        'status': 503,
        'body': {'error': 'HTTPConnectionPool(host=127.0.0.1, port=8080): Max retries exceeded'},
    })

    args = SimpleNamespace(
        message='hello',
        max_tokens=32,
        temperature=0.2,
        session_id='sess-offline',
        manual=False,
        auto=False,
        manual_model='',
        manual_provider='',
        persist=False,
    )

    try:
        skill.cmd_chat(args)
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out.strip()

    assert out == '⚠️ AIchain daemon is offline or warming up. Please ensure it is running.'

def test_build_chat_payload_defaults_to_openclaw_session_id():
    args = SimpleNamespace(
        message='hello',
        max_tokens=32,
        temperature=0.2,
        session_id='',
        manual=False,
        auto=False,
        manual_model='',
        manual_provider='',
        persist=False,
    )

    payload = skill.build_chat_payload(args)

    assert payload['session_id'] == skill.DEFAULT_OPENCLAW_SESSION_ID
