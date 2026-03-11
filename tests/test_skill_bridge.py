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
