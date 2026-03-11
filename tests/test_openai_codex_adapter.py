#!/usr/bin/env python3
"""Tests for the OpenAI Codex OAuth adapter and codex access/runtime wiring."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import aichaind.main as daemon_main
import aichaind.transport.http_server as http_server
from aichaind.providers.access import build_provider_access_layer, ProviderAccessDecision
from aichaind.providers.adapters import openai_codex
from aichaind.providers.adapters.openai_codex import OpenAICodexOAuthAdapter
from aichaind.providers.base import CompletionRequest, DiscoveryResult
from aichaind.providers.discovery import DiscoveryReport
from aichaind.routing.rules import RouteDecision


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def _config_payload() -> dict:
    return {
        'gateway': {
            'port': 18789,
            'auth': {'token': 'gateway-token'},
            'remote': {'url': 'ws://127.0.0.1:18789'},
            'http': {
                'endpoints': {
                    'chatCompletions': {'enabled': True},
                    'responses': {'enabled': False},
                }
            },
        }
    }


def _auth_payload() -> dict:
    return {
        'profiles': {
            'openai-codex:default': {
                'provider': 'openai-codex',
                'type': 'oauth',
                'access': 'oauth-access-token',
                'accountId': 'acct-123',
            }
        },
        'lastGood': {
            'openai-codex': 'openai-codex:default'
        },
    }



def test_codex_cli_resolution_prefers_resolved_command(monkeypatch):
    calls = {}

    class Completed:
        returncode = 0
        stdout = json.dumps({'models': [{'key': 'openai-codex/gpt-5.4', 'available': True}]})
        stderr = ''

    monkeypatch.setattr(openai_codex, '_resolve_openclaw_cli_command', lambda: ['C:/Users/test/openclaw.cmd'])

    def fake_run(cmd, capture_output=None, text=None, timeout=None, check=None):
        calls['cmd'] = cmd
        return Completed()

    monkeypatch.setattr(openai_codex.subprocess, 'run', fake_run)

    models = openai_codex._list_models_from_openclaw_cli()

    assert models == ['openai-codex/gpt-5.4']
    assert calls['cmd'][0] == 'C:/Users/test/openclaw.cmd'
    assert calls['cmd'][1:] == ['models', 'list', '--all', '--provider', 'openai-codex', '--json']

def test_codex_adapter_discovers_models_and_marks_target_form_not_reached(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / 'openclaw.json'
    auth_path = tmp_path / 'auth-profiles.json'
    _write_json(cfg_path, _config_payload())
    _write_json(auth_path, _auth_payload())

    monkeypatch.setattr(openai_codex, '_list_models_from_openclaw_cli', lambda: ['openai-codex/gpt-5.3-codex'])

    class ProbeResponse:
        status_code = 404

        def json(self):
            return {}

    monkeypatch.setattr(openai_codex, 'requests', SimpleNamespace(post=lambda *args, **kwargs: ProbeResponse(), Timeout=TimeoutError))

    adapter = OpenAICodexOAuthAdapter(config_path=cfg_path, auth_profiles_path=auth_path)
    result = adapter.discover()

    assert result.status == 'authenticated'
    assert result.available_models == ['openai-codex/gpt-5.3-codex']
    assert result.limits['preferred_model'] == 'openai-codex/gpt-5.3-codex'
    assert result.limits['target_form_reached'] is False
    assert result.limits['target_probe_status'] == 'unverified'
    assert result.limits['chat_endpoint_enabled'] is True


def test_codex_adapter_prefers_target_model_when_available(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / 'openclaw.json'
    auth_path = tmp_path / 'auth-profiles.json'
    _write_json(cfg_path, _config_payload())
    _write_json(auth_path, _auth_payload())
    monkeypatch.setattr(openai_codex, '_list_models_from_openclaw_cli', lambda: [])

    adapter = OpenAICodexOAuthAdapter(config_path=cfg_path, auth_profiles_path=auth_path)
    resolved = adapter.resolve_preferred_model('openai/gpt-5.4', [
        'openai-codex/gpt-5.3-codex',
        'openai-codex/gpt-5.4',
    ])

    assert resolved == 'openai-codex/gpt-5.4'


def test_codex_adapter_executes_via_openclaw_gateway_chat_completions(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / 'openclaw.json'
    auth_path = tmp_path / 'auth-profiles.json'
    _write_json(cfg_path, _config_payload())
    _write_json(auth_path, _auth_payload())
    monkeypatch.setattr(openai_codex, '_list_models_from_openclaw_cli', lambda: ['openai-codex/gpt-5.3-codex'])

    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, model='openai-codex/gpt-5.4'):
            self._model = model

        def json(self):
            return {
                'choices': [{'message': {'content': 'OAUTH_GATEWAY_OK'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 4},
                'model': self._model,
            }

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        return FakeResponse(model=json['model'])

    monkeypatch.setattr(openai_codex, 'requests', SimpleNamespace(post=fake_post, Timeout=TimeoutError))

    adapter = OpenAICodexOAuthAdapter(config_path=cfg_path, auth_profiles_path=auth_path)
    response = adapter.execute(CompletionRequest(
        model='openai/gpt-5.4',
        messages=[{'role': 'user', 'content': 'Say hi'}],
        max_tokens=64,
    ))

    assert response.status == 'success'
    assert response.content == 'OAUTH_GATEWAY_OK'
    assert calls[-1]['url'] == 'http://127.0.0.1:18789/v1/chat/completions'
    assert calls[-1]['json']['model'] == 'openai-codex/gpt-5.4'
    assert calls[-1]['headers']['Authorization'] == 'Bearer gateway-token'
    assert calls[-1]['headers']['X-OpenClaw-Token'] == 'gateway-token'



def test_codex_adapter_discovers_target_form_via_runtime_probe(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / 'openclaw.json'
    auth_path = tmp_path / 'auth-profiles.json'
    _write_json(cfg_path, _config_payload())
    _write_json(auth_path, _auth_payload())

    monkeypatch.setattr(openai_codex, '_list_models_from_openclaw_cli', lambda: ['openai-codex/gpt-5.3-codex'])

    class ProbeResponse:
        status_code = 200

        def json(self):
            return {
                'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
                'model': 'openai-codex/gpt-5.4',
            }

    monkeypatch.setattr(openai_codex, 'requests', SimpleNamespace(post=lambda *args, **kwargs: ProbeResponse(), Timeout=TimeoutError))

    adapter = OpenAICodexOAuthAdapter(config_path=cfg_path, auth_profiles_path=auth_path)
    result = adapter.discover()

    assert result.status == 'authenticated'
    assert 'openai-codex/gpt-5.4' in result.available_models
    assert result.limits['preferred_model'] == 'openai-codex/gpt-5.4'
    assert result.limits['target_form_reached'] is True
    assert result.limits['target_probe_status'] == 'ok'


def test_discover_provider_capabilities_accepts_oauth_adapter_and_marks_target_form(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / 'openclaw.json'
    _write_json(cfg_path, {
        'auth': {'profiles': {'openai-codex:default': {'provider': 'openai-codex'}}},
    })
    cfg = {
        'openclaw_config': str(cfg_path),
        'local_execution': {'enabled': False},
        'provider_access': {
            'providers': {
                'openai-codex': {
                    'enabled_methods': ['oauth'],
                    'oauth': {
                        'official_support': True,
                        'technically_stable': True,
                        'provider_compliant': True,
                        'adapter_enabled': True,
                    },
                }
            }
        },
    }
    layer = build_provider_access_layer(cfg, DiscoveryReport())

    class FakeCodexAdapter:
        name = 'openai-codex'

        def supports_access_method(self, method: str) -> bool:
            return method == 'oauth'

        def discover(self):
            return DiscoveryResult(
                status='authenticated',
                available_models=['openai-codex/gpt-5.3-codex'],
                limits={'target_form_reached': False},
            )

    monkeypatch.setattr('aichaind.providers.registry.get_adapter', lambda provider: FakeCodexAdapter() if provider == 'openai-codex' else None)

    capabilities = daemon_main.discover_provider_capabilities(layer, DiscoveryReport(), logging.getLogger('test'))
    decision = layer.resolve('openai-codex')

    assert capabilities['openai-codex'] == {'openai-codex/gpt-5.3-codex'}
    assert decision.runtime_confirmed is True
    assert decision.target_form_reached is False
    assert decision.status == 'target_form_not_reached'


def test_http_server_routes_openai_gpt5_family_to_verified_codex_oauth(monkeypatch):
    route_decision = RouteDecision(
        target_model='openai/gpt-5.4',
        target_provider='openai',
        confidence=0.93,
        decision_layers=['L3:encoder'],
        reason='high_intelligence_route',
    )

    http_server._provider_access_layer = SimpleNamespace(
        resolve=lambda provider: ProviderAccessDecision(
            provider=provider,
            selected_method='oauth' if provider == 'openai-codex' else 'api_key',
            status='runtime_confirmed' if provider == 'openai-codex' else 'runtime_confirmed',
            reason='discover:authenticated:models=2',
            runtime_confirmed=True,
            target_form_reached=True,
        )
    )

    class FakeCodexAdapter:
        def discover(self):
            return DiscoveryResult(
                status='authenticated',
                available_models=['openai-codex/gpt-5.3-codex', 'openai-codex/gpt-5.4'],
                limits={'target_form_reached': True},
            )

        def resolve_preferred_model(self, requested='', available_models=None):
            assert requested == 'openai/gpt-5.4'
            assert 'openai-codex/gpt-5.4' in (available_models or [])
            return 'openai-codex/gpt-5.4'

    monkeypatch.setattr(http_server, 'get_adapter', lambda provider: FakeCodexAdapter() if provider == 'openai-codex' else None)

    updated, model, provider, used = http_server._maybe_route_openai_codex_oauth(
        route_decision,
        'openai/gpt-5.4',
        'openai',
    )

    assert used is True
    assert provider == 'openai-codex'
    assert model == 'openai-codex/gpt-5.4'
    assert updated.target_provider == 'openai-codex'
    assert updated.cost_tier == 'oauth_window'


def test_http_server_codex_oauth_keeps_fallback_when_target_form_not_reached(monkeypatch):
    route_decision = RouteDecision(
        target_model='openai/gpt-5.4',
        target_provider='openai',
        confidence=0.93,
        decision_layers=['L3:encoder'],
        reason='high_intelligence_route',
    )

    http_server._provider_access_layer = SimpleNamespace(
        resolve=lambda provider: ProviderAccessDecision(
            provider=provider,
            selected_method='oauth' if provider == 'openai-codex' else 'api_key',
            status='target_form_not_reached' if provider == 'openai-codex' else 'runtime_confirmed',
            reason='discover:authenticated:models=1',
            runtime_confirmed=True,
            target_form_reached=False if provider == 'openai-codex' else True,
        )
    )

    class FallbackCodexAdapter:
        def discover(self):
            return DiscoveryResult(
                status='authenticated',
                available_models=['openai-codex/gpt-5.3-codex'],
                limits={'target_form_reached': False},
            )

        def resolve_preferred_model(self, requested='', available_models=None):
            assert requested == 'openai/gpt-5.4'
            return 'openai-codex/gpt-5.3-codex'

    monkeypatch.setattr(http_server, 'get_adapter', lambda provider: FallbackCodexAdapter() if provider == 'openai-codex' else None)

    updated, model, provider, used = http_server._maybe_route_openai_codex_oauth(
        route_decision,
        'openai/gpt-5.4',
        'openai',
    )

    assert used is True
    assert provider == 'openai-codex'
    assert model == 'openai-codex/gpt-5.3-codex'
    assert updated.target_provider == 'openai-codex'
    assert updated.cost_tier == 'oauth_window'
