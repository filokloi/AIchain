#!/usr/bin/env python3
"""Business-level scenario matrix tests for live-routing expectations."""

from tools.verify_system_scenarios import (
    build_expected_routing,
    classify_provider,
    detect_live_feature_set,
    verify_case,
)


class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = ''

    def json(self):
        return self._body


def test_build_expected_routing_prefers_runtime_confirmed_codex_for_coding():
    health = {
        'provider_access': {
            'openai-codex': {
                'runtime_confirmed': True,
                'target_form_reached': True,
                'billing_basis': 'subscription_plan_window',
            }
        },
        'routing_preferences': {
            'prefer_prepaid_premium': True,
            'prepaid_premium_providers': ['openai-codex'],
        },
        'local_brain': 'lmstudio/qwen/qwen3-4b-thinking-2507',
        'local_profiles': {
            'active_profile': {
                'runtime_confirmed': True,
                'prompt_type_suitability': {'coding': 100.0},
                'task_profiles': {'coding': {'success': True}},
            }
        },
    }

    expectation = build_expected_routing(health, {'name': 'coding'})

    assert expectation.provider_class == 'cloud'
    assert expectation.provider == 'openai-codex'
    assert expectation.model_contains == 'openai-codex/gpt-5.4'


def test_build_expected_routing_falls_back_to_local_when_codex_is_not_ready_and_local_is_strong():
    health = {
        'provider_access': {
            'openai-codex': {
                'runtime_confirmed': False,
                'target_form_reached': False,
            }
        },
        'local_brain': 'lmstudio/qwen/qwen3-4b-thinking-2507',
        'local_profiles': {
            'active_profile': {
                'runtime_confirmed': True,
                'prompt_type_suitability': {'coding': 95.0},
                'task_profiles': {'coding': {'success': True}},
            }
        },
    }

    expectation = build_expected_routing(health, {'name': 'coding'})

    assert expectation.provider_class == 'local'
    assert expectation.provider == 'lmstudio'
    assert 'qwen3-4b-thinking-2507' in expectation.model_contains


def test_build_expected_routing_keeps_pii_cloud_allowed_by_default():
    expectation = build_expected_routing({}, {'name': 'pii_cloud_allowed'})

    assert expectation.provider_class == 'cloud'
    assert expectation.pii_detected is True
    assert expectation.pii_redacted is False
    assert expectation.local_reroute_used is False


def test_detect_live_feature_set_flags_missing_sections():
    result = detect_live_feature_set('<html><body>catalog_manifest.json</body></html>')

    assert result['has_provider_access_panel'] is False
    assert result['has_self_hosted_panel'] is False


def test_classify_provider_recognizes_local_runtimes():
    assert classify_provider('lmstudio') == 'local'
    assert classify_provider('deepseek') == 'cloud'


def test_verify_case_reports_pii_semantics_without_redaction(monkeypatch):
    body = {
        'choices': [{'message': {'content': 'PII_PATH_OK'}}],
        '_aichaind': {
            'routed_provider': 'deepseek',
            'routed_model': 'deepseek/deepseek-chat',
            'provider_access_method': 'api_key',
            'route_layers': ['L1:heuristic'],
            'pii_detected': True,
            'pii_redacted': False,
            'local_reroute_used': False,
        },
    }
    monkeypatch.setattr('tools.verify_system_scenarios.requests.post', lambda *args, **kwargs: _FakeResponse(200, body))
    result = verify_case(
        'http://127.0.0.1:8080',
        'token',
        {
            'name': 'pii_cloud_allowed',
            'prompt': 'My SSN is 123-45-6789. Reply exactly PII_PATH_OK.',
            'max_tokens': 24,
        },
        {},
    )

    assert result.ok is True
    assert result.pii_detected is True
    assert result.pii_redacted is False
    assert result.provider_class == 'cloud'


def test_verify_case_requires_codex_target_model_for_coding(monkeypatch):
    body = {
        'choices': [{'message': {'content': 'def add(a, b):\n    return a + b'}}],
        '_aichaind': {
            'routed_provider': 'openai-codex',
            'routed_model': 'openai-codex/gpt-5.4',
            'provider_access_method': 'oauth',
            'route_layers': ['L2:semantic:code_generation'],
        },
    }
    monkeypatch.setattr('tools.verify_system_scenarios.requests.post', lambda *args, **kwargs: _FakeResponse(200, body))
    health = {
        'provider_access': {
            'openai-codex': {
                'runtime_confirmed': True,
                'target_form_reached': True,
                'billing_basis': 'subscription_plan_window',
            }
        },
        'routing_preferences': {
            'prefer_prepaid_premium': True,
            'prepaid_premium_providers': ['openai-codex'],
        }
    }
    result = verify_case(
        'http://127.0.0.1:8080',
        'token',
        {
            'name': 'coding',
            'prompt': 'Write only Python code for a function add(a, b) with a unit test.',
            'max_tokens': 140,
        },
        health,
    )

    assert result.ok is True
    assert result.routed_provider == 'openai-codex'
    assert 'gpt-5.4' in result.routed_model
