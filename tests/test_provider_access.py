#!/usr/bin/env python3
"""Tests for aichaind.providers.access and access-driven execution fallback."""

import json
from pathlib import Path
from types import SimpleNamespace

from aichaind.providers.access import build_provider_access_layer
from aichaind.providers.discovery import DiscoveryReport, ProviderCredential
from aichaind.providers.balance import BalanceReport, ProviderBalance
from aichaind.routing.cost_optimizer import CostOptimizer
from aichaind.routing.rules import RouteDecision
import aichaind.transport.http_server as http_server


def _write_openclaw_config(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def test_provider_access_prefers_api_key_over_configured_oauth_profile(tmp_path: Path):
    cfg_path = tmp_path / 'openclaw.json'
    _write_openclaw_config(cfg_path, {
        'auth': {
            'profiles': {
                'google-consumer': {'provider': 'google'}
            }
        }
    })
    cfg = {
        'openclaw_config': str(cfg_path),
        'local_execution': {'enabled': False},
        'provider_access': {
            'providers': {
                'google': {
                    'enabled_methods': ['api_key', 'oauth'],
                    'oauth': {
                        'official_support': True,
                        'technically_stable': True,
                        'provider_compliant': True,
                        'adapter_enabled': False,
                    },
                }
            }
        },
    }
    report = DiscoveryReport(credentials=[
        ProviderCredential(provider='google', api_key='AIza1234567890123456789012', source='env_var', has_subscription=True, priority=10)
    ])

    layer = build_provider_access_layer(cfg, report)
    decision = layer.resolve('google')

    assert decision.selected_method == 'api_key'
    assert 'api_key' in decision.configured_methods
    assert 'oauth' in decision.configured_methods
    assert decision.status == 'configured'


def test_provider_access_keeps_unofficial_oauth_disabled(tmp_path: Path):
    cfg_path = tmp_path / 'openclaw.json'
    _write_openclaw_config(cfg_path, {
        'auth': {
            'profiles': {
                'google-consumer': {'provider': 'google'}
            }
        }
    })
    cfg = {
        'openclaw_config': str(cfg_path),
        'local_execution': {'enabled': False},
        'provider_access': {'providers': {'google': {'enabled_methods': ['oauth']}}},
    }

    layer = build_provider_access_layer(cfg, DiscoveryReport())
    decision = layer.resolve('google')

    assert decision.selected_method == 'disabled'
    assert decision.reason == 'oauth:not_officially_supported'
    assert 'oauth' in decision.configured_methods


def test_provider_access_selects_local_runtime_when_enabled(tmp_path: Path):
    cfg_path = tmp_path / 'openclaw.json'
    _write_openclaw_config(cfg_path, {})
    cfg = {
        'openclaw_config': str(cfg_path),
        'local_execution': {
            'enabled': True,
            'provider': 'lmstudio',
            'base_url': 'http://127.0.0.1:1234/v1',
            'default_model': 'qwen2.5-coder',
        },
        'provider_access': {'providers': {'lmstudio': {'enabled_methods': ['local']}}},
    }

    layer = build_provider_access_layer(cfg, DiscoveryReport())
    decision = layer.resolve('lmstudio')

    assert decision.selected_method == 'local'
    assert decision.status == 'configured'
    assert decision.target_form_reached is True


def test_workspace_connector_is_tracked_but_not_selected_without_adapter_enablement(tmp_path: Path):
    cfg_path = tmp_path / 'openclaw.json'
    _write_openclaw_config(cfg_path, {
        'connectors': {
            'openai-workspace': {'provider': 'openai', 'type': 'workspace'}
        }
    })
    cfg = {
        'openclaw_config': str(cfg_path),
        'local_execution': {'enabled': False},
        'provider_access': {
            'providers': {
                'openai': {
                    'enabled_methods': ['workspace_connector'],
                    'workspace_connector': {
                        'official_support': True,
                        'technically_stable': True,
                        'provider_compliant': True,
                        'adapter_enabled': False,
                    },
                }
            }
        },
    }

    layer = build_provider_access_layer(cfg, DiscoveryReport())
    decision = layer.resolve('openai')

    assert decision.selected_method == 'disabled'
    assert decision.reason == 'workspace_connector:adapter_not_enabled'


def test_ensure_provider_access_fails_over_when_target_form_not_reached(tmp_path: Path):
    cfg_path = tmp_path / 'openclaw.json'
    _write_openclaw_config(cfg_path, {})
    cfg = {
        'openclaw_config': str(cfg_path),
        'local_execution': {'enabled': False},
        'provider_access': {
            'providers': {
                'google': {'enabled_methods': ['api_key']},
                'deepseek': {'enabled_methods': ['api_key']},
            }
        },
    }
    report = DiscoveryReport(credentials=[
        ProviderCredential(provider='google', api_key='AIza1234567890123456789012', source='env_var', has_subscription=True, priority=10),
        ProviderCredential(provider='deepseek', api_key='sk-deepseek-example', source='env_var', priority=20),
    ])
    layer = build_provider_access_layer(cfg, report)
    layer.mark_runtime_result('google', False, 'discover:auth_failed:models=0')

    optimizer = CostOptimizer({})
    optimizer.configure_provider_capabilities({
        'google': set(),
        'deepseek': {'deepseek/deepseek-chat'},
    })
    http_server._provider_access_layer = layer
    http_server._cascade_router = SimpleNamespace(_cost_optimizer=optimizer)
    http_server._roles = {
        'fast_brain': 'google/gemini-2.5-flash',
        'heavy_brain': 'openai/gpt-4.1',
        'visual_brain': 'openai/gpt-4o',
    }

    decision = RouteDecision(
        target_model='google/gemini-2.5-flash',
        target_provider='google',
        confidence=0.82,
        decision_layers=['L1:heuristic'],
        reason='quick_general',
    )
    balance_report = BalanceReport(
        balances={
            'google': ProviderBalance(provider='google', has_credits=True, is_subscription=True, is_free_tier=True, source='estimated'),
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=5.0, source='api'),
        },
        providers_with_credits=['google', 'deepseek'],
    )

    updated, model, provider, access_decision, failover_used, block_reason = http_server._ensure_provider_access(
        decision=decision,
        payload={'max_tokens': 64},
        target_model='google/gemini-2.5-flash',
        target_provider='google',
        balance_report=balance_report,
    )

    assert failover_used is True
    assert block_reason == ''
    assert provider == 'deepseek'
    assert model == 'deepseek/deepseek-chat'
    assert updated.target_provider == 'deepseek'
    assert access_decision.selected_method == 'api_key'


def test_ensure_provider_access_fails_over_when_selected_provider_is_disabled(tmp_path: Path):
    cfg_path = tmp_path / 'openclaw.json'
    _write_openclaw_config(cfg_path, {})
    cfg = {
        'openclaw_config': str(cfg_path),
        'local_execution': {'enabled': False},
        'provider_access': {
            'providers': {
                'google': {'enabled_methods': []},
                'deepseek': {'enabled_methods': ['api_key']},
            }
        },
    }
    report = DiscoveryReport(credentials=[
        ProviderCredential(provider='deepseek', api_key='sk-deepseek-example', source='env_var', priority=20),
    ])
    layer = build_provider_access_layer(cfg, report)

    optimizer = CostOptimizer({})
    optimizer.configure_provider_capabilities({'deepseek': {'deepseek/deepseek-chat'}})
    http_server._provider_access_layer = layer
    http_server._cascade_router = SimpleNamespace(_cost_optimizer=optimizer)
    http_server._roles = {
        'fast_brain': 'google/gemini-2.5-flash',
        'heavy_brain': 'openai/gpt-4.1',
        'visual_brain': 'openai/gpt-4o',
    }

    decision = RouteDecision(
        target_model='google/gemini-2.5-flash',
        target_provider='google',
        confidence=0.82,
        decision_layers=['L1:heuristic'],
        reason='quick_general',
    )
    balance_report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=5.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    updated, model, provider, access_decision, failover_used, block_reason = http_server._ensure_provider_access(
        decision=decision,
        payload={'max_tokens': 64},
        target_model='google/gemini-2.5-flash',
        target_provider='google',
        balance_report=balance_report,
    )

    assert failover_used is True
    assert block_reason == ''
    assert provider == 'deepseek'
    assert model == 'deepseek/deepseek-chat'
    assert updated.target_provider == 'deepseek'
    assert access_decision.selected_method == 'api_key'


def test_provider_access_surfaces_oauth_limit_metadata_even_when_adapter_not_enabled(tmp_path: Path):
    cfg_path = tmp_path / 'openclaw.json'
    _write_openclaw_config(cfg_path, {
        'auth': {
            'profiles': {
                'openai-codex:default': {'provider': 'openai-codex'}
            }
        }
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
                        'adapter_enabled': False,
                        'billing_basis': 'subscription_plan_window',
                        'usage_tracking': 'openclaw_auth_usage_stats_plus_provider_ui',
                        'quota_visibility': 'provider_ui_or_openclaw_sign_in_window_not_fully_machine_readable',
                        'limitations': [
                            'Daily and weekly sign-in plan windows may apply.',
                        ],
                        'project_verification': 'Verified in OpenClaw; adapter path still pending.',
                    },
                }
            }
        },
    }

    layer = build_provider_access_layer(cfg, DiscoveryReport())
    decision = layer.resolve('openai-codex')

    assert decision.selected_method == 'disabled'
    assert decision.reason == 'oauth:adapter_not_enabled'
    assert decision.billing_basis == 'subscription_plan_window'
    assert decision.usage_tracking == 'openclaw_auth_usage_stats_plus_provider_ui'
    assert decision.quota_visibility == 'provider_ui_or_openclaw_sign_in_window_not_fully_machine_readable'
    assert decision.limitations == ['Daily and weekly sign-in plan windows may apply.']
    assert decision.project_verification == 'Verified in OpenClaw; adapter path still pending.'

