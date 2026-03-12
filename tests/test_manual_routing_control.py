#!/usr/bin/env python3
"""Tests for manual routing mode and model lock behavior."""

from types import SimpleNamespace

from aichaind.core.session import CanonicalSession
from aichaind.routing.rules import RouteDecision
import aichaind.transport.http_server as http_server


class _AccessDecision:
    def __init__(self, selected_method='disabled', status='disabled', runtime_confirmed=False, reason='disabled'):
        self.selected_method = selected_method
        self.status = status
        self.runtime_confirmed = runtime_confirmed
        self.reason = reason

    def to_dict(self):
        return {
            'selected_method': self.selected_method,
            'status': self.status,
            'runtime_confirmed': self.runtime_confirmed,
            'reason': self.reason,
        }


class _AccessLayer:
    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, provider):
        return self.mapping.get(provider, _AccessDecision())


def test_resolve_routing_control_uses_session_manual_lock():
    session = CanonicalSession(
        session_id='sess-1',
        routing_mode='manual',
        locked_model='openai-codex/gpt-5.4',
        locked_provider='openai-codex',
    )

    control, error, changed = http_server._resolve_routing_control(session, {'messages': []})

    assert error == ''
    assert changed is False
    assert control['manual_override_active'] is True
    assert control['locked_model'] == 'openai-codex/gpt-5.4'
    assert control['locked_provider'] == 'openai-codex'


def test_resolve_routing_control_persists_manual_lock():
    session = CanonicalSession(session_id='sess-2')
    payload = {
        '_aichain_control': {
            'mode': 'manual',
            'model': 'openai-codex/gpt-5.4',
            'provider': 'openai-codex',
            'persist_for_session': True,
        }
    }

    control, error, changed = http_server._resolve_routing_control(session, payload)

    assert error == ''
    assert changed is True
    assert control['manual_override_active'] is True
    assert session.routing_mode == 'manual'
    assert session.locked_model == 'openai-codex/gpt-5.4'
    assert session.locked_provider == 'openai-codex'


def test_resolve_routing_control_can_return_to_auto():
    session = CanonicalSession(
        session_id='sess-3',
        routing_mode='manual',
        locked_model='openai-codex/gpt-5.4',
        locked_provider='openai-codex',
    )
    payload = {'_aichain_control': {'mode': 'auto', 'persist_for_session': True}}

    control, error, changed = http_server._resolve_routing_control(session, payload)

    assert error == ''
    assert changed is True
    assert control['manual_override_active'] is False
    assert session.routing_mode == 'auto'
    assert session.locked_model == ''
    assert session.locked_provider == ''


def test_build_manual_route_decision_marks_manual_mode():
    decision, model, provider = http_server._build_manual_route_decision({
        'locked_model': 'openai-codex/gpt-5.4',
        'locked_provider': 'openai-codex',
    })

    assert isinstance(decision, RouteDecision)
    assert model == 'openai-codex/gpt-5.4'
    assert provider == 'openai-codex'
    assert decision.decision_layers == ['manual_override']
    assert decision.reason == 'manual_override'
    assert decision.cost_tier == 'manual'


def test_ensure_provider_access_does_not_failover_when_manual_override_active(monkeypatch):
    monkeypatch.setattr(
        http_server,
        '_provider_access_layer',
        _AccessLayer({'openai': _AccessDecision(reason='oauth_disabled')}),
    )
    monkeypatch.setattr(
        http_server,
        '_cascade_router',
        SimpleNamespace(_cost_optimizer=object()),
    )

    decision = RouteDecision(target_model='openai/gpt-5.4', target_provider='openai', confidence=1.0)
    result = http_server._ensure_provider_access(
        decision=decision,
        payload={'max_tokens': 100},
        target_model='openai/gpt-5.4',
        target_provider='openai',
        balance_report=SimpleNamespace(),
        allow_failover=False,
    )

    _, model, provider, access_decision, failover_used, block_reason = result
    assert model == 'openai/gpt-5.4'
    assert provider == 'openai'
    assert access_decision.reason == 'oauth_disabled'
    assert failover_used is False
    assert block_reason == 'provider_access_unavailable:openai:oauth_disabled'
