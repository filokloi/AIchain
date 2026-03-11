#!/usr/bin/env python3
"""Tests for aichaind.routing.cost_optimizer."""

import pytest

from aichaind.providers.balance import BalanceReport, ProviderBalance
from aichaind.routing.cost_optimizer import CostOptimizer


class TestCostOptimizer:
    def test_loads_pricing_from_routing_hierarchy(self):
        optimizer = CostOptimizer({
            "routing_hierarchy": [
                {
                    "model": "openai/gpt-4o",
                    "tier": "OAUTH_BRIDGE",
                    "provider": "OpenAI",
                    "metrics": {"cost": 0.00001},
                }
            ]
        })

        assert "openai/gpt-4o" in optimizer._pricing
        assert optimizer._pricing["openai/gpt-4o"].input_cost_per_1k == pytest.approx(0.01)

    def test_subscription_leverage_prefers_verified_google_access(self):
        optimizer = CostOptimizer({"routing_hierarchy": []})
        report = BalanceReport(
            balances={
                "google": ProviderBalance(
                    provider="google",
                    has_credits=True,
                    is_subscription=True,
                    is_free_tier=True,
                    source="estimated",
                ),
            },
            providers_with_credits=["google"],
        )

        result = optimizer.optimize(
            model_preference="free",
            balance_report=report,
            available_models={"free": "openrouter/google/gemini-2.5-flash:free"},
            estimated_tokens=256,
        )

        assert result.provider == "google"
        assert result.model == "google/gemini-2.5-flash"
        assert result.tier == "subscription"

    def test_failed_direct_provider_discovery_prevents_subscription_leverage(self):
        optimizer = CostOptimizer({"routing_hierarchy": []})
        optimizer.configure_provider_capabilities({
            "google": set(),
            "groq": {"groq/llama-3.1-8b-instant"},
        })
        report = BalanceReport(
            balances={
                "google": ProviderBalance(
                    provider="google",
                    has_credits=True,
                    is_subscription=True,
                    is_free_tier=True,
                    source="estimated",
                ),
                "groq": ProviderBalance(
                    provider="groq",
                    has_credits=True,
                    is_free_tier=True,
                    source="estimated",
                ),
            },
            providers_with_credits=["google", "groq"],
        )

        result = optimizer.optimize(
            model_preference="free",
            balance_report=report,
            available_models={"free": "openrouter/google/gemini-2.5-flash:free"},
            estimated_tokens=256,
            exclude_providers={"google"},
        )

        assert result.provider == "groq"
        assert result.model == "groq/llama-3.1-8b-instant"
        assert result.reason == "verified_direct_fallback:groq"

    def test_prefers_verified_direct_provider_over_openrouter_when_both_have_credits(self):
        optimizer = CostOptimizer({
            "routing_hierarchy": [
                {
                    "model": "openai/gpt-4o",
                    "tier": "OAUTH_BRIDGE",
                    "provider": "OpenAI",
                    "metrics": {"cost": 0.00002},
                },
                {
                    "model": "qwen/qwen3-coder",
                    "tier": "HEAVY_HITTER",
                    "provider": "OpenRouter",
                    "metrics": {"cost": 0.000001},
                },
            ]
        })
        report = BalanceReport(
            balances={
                "openai": ProviderBalance(provider="openai", has_credits=True, balance_usd=5.0, source="api"),
                "openrouter": ProviderBalance(provider="openrouter", has_credits=True, balance_usd=5.0, source="api"),
            },
            providers_with_credits=["openai", "openrouter"],
        )

        result = optimizer.optimize(
            model_preference="heavy",
            balance_report=report,
            available_models={
                "heavy": "openrouter/google/gemini-2.5-pro",
                "free": "openrouter/google/gemini-2.5-flash:free",
            },
            estimated_tokens=1000,
        )

        assert result.provider == "openai"
        assert result.model == "openai/gpt-4o"
        assert result.reason == "cost_optimized:openai"

    def test_estimated_openai_subscription_does_not_override_verified_google(self):
        optimizer = CostOptimizer({
            "routing_hierarchy": [
                {
                    "model": "google/gemini-2.5-flash",
                    "tier": "OAUTH_BRIDGE",
                    "provider": "Google",
                    "metrics": {"cost": 0.000005},
                },
                {
                    "model": "openai/gpt-4o",
                    "tier": "OAUTH_BRIDGE",
                    "provider": "OpenAI",
                    "metrics": {"cost": 0.00001},
                },
            ]
        })
        report = BalanceReport(
            balances={
                "openai": ProviderBalance(provider="openai", has_credits=True, is_subscription=True, source="estimated"),
                "google": ProviderBalance(provider="google", has_credits=True, is_free_tier=True, source="estimated"),
            },
            providers_with_credits=["openai", "google"],
        )

        result = optimizer.optimize(
            model_preference="free",
            balance_report=report,
            available_models={"free": "openai/o3-pro"},
        )

        assert result.provider == "google"
        assert result.model == "google/gemini-2.5-flash"

    def test_filters_out_models_not_in_provider_capability_set(self):
        optimizer = CostOptimizer({
            "routing_hierarchy": [
                {
                    "model": "openai/codex-mini",
                    "tier": "OAUTH_BRIDGE",
                    "provider": "OpenAI",
                    "metrics": {"cost": 0.00001},
                },
                {
                    "model": "openai/gpt-4.1",
                    "tier": "OAUTH_BRIDGE",
                    "provider": "OpenAI",
                    "metrics": {"cost": 0.00002},
                },
            ]
        })
        optimizer.configure_provider_capabilities({
            "openai": {"openai/gpt-4.1"},
        })
        report = BalanceReport(
            balances={
                "openai": ProviderBalance(provider="openai", has_credits=True, balance_usd=2.0, source="api"),
            },
            providers_with_credits=["openai"],
        )

        result = optimizer.optimize(
            model_preference="heavy",
            balance_report=report,
            available_models={"heavy": "openai/gpt-4.1"},
        )

        assert result.model == "openai/gpt-4.1"
        assert result.provider == "openai"

    def test_no_credits_falls_back_to_free_model(self):
        optimizer = CostOptimizer({"routing_hierarchy": []})
        report = BalanceReport(
            balances={
                "openrouter": ProviderBalance(provider="openrouter", has_credits=False, balance_usd=0.0),
            },
            providers_empty=["openrouter"],
        )

        result = optimizer.optimize(
            model_preference="heavy",
            balance_report=report,
            available_models={
                "heavy": "openai/o3-pro",
                "free": "openrouter/google/gemini-2.5-flash:free",
            },
        )

        assert result.model == "openrouter/google/gemini-2.5-flash:free"
        assert result.tier == "free"
        assert optimizer.alerts[-1]["type"] == "no_credits"

    def test_visual_route_is_preserved_when_provider_is_verified(self):
        optimizer = CostOptimizer({"routing_hierarchy": []})
        report = BalanceReport(
            balances={
                "openrouter": ProviderBalance(provider="openrouter", has_credits=True, balance_usd=4.0, source="api"),
            },
            providers_with_credits=["openrouter"],
        )

        result = optimizer.optimize(
            model_preference="visual",
            balance_report=report,
            available_models={"visual": "openrouter/openai/gpt-4o"},
        )

        assert result.model == "openrouter/openai/gpt-4o"
        assert result.provider == "openrouter"
        assert result.reason == "visual_route_preserved"

    def test_verified_direct_fallback_beats_dead_legacy_openrouter_free_default(self):
        optimizer = CostOptimizer({"routing_hierarchy": []})
        optimizer.configure_provider_capabilities({
            "groq": {"groq/llama-3.1-8b-instant"},
        })
        report = BalanceReport(
            balances={
                "google": ProviderBalance(provider="google", has_credits=True, is_subscription=True, is_free_tier=True, source="estimated"),
                "openai": ProviderBalance(provider="openai", has_credits=True, is_subscription=True, source="estimated"),
                "groq": ProviderBalance(provider="groq", has_credits=True, is_free_tier=True, source="estimated"),
                "openrouter": ProviderBalance(provider="openrouter", has_credits=False, balance_usd=0.0, source="api"),
            },
            providers_with_credits=["google", "openai", "groq"],
        )

        result = optimizer.optimize(
            model_preference="free",
            balance_report=report,
            available_models={"free": "openrouter/google/gemini-2.5-flash:free"},
            exclude_providers={"google", "openai"},
        )

        assert result.provider == "groq"
        assert result.model == "groq/llama-3.1-8b-instant"
        assert result.reason == "verified_direct_fallback:groq"



def test_zero_marginal_access_does_not_override_catalog_first_free_route():
    class _Decision:
        def __init__(self, provider, selected_method='disabled', runtime_confirmed=False):
            self.provider = provider
            self.selected_method = selected_method
            self.status = 'runtime_confirmed' if runtime_confirmed else 'disabled'
            self.reason = ''
            self.runtime_confirmed = runtime_confirmed
            self.target_form_reached = runtime_confirmed
            self.quota_visibility = 'provider_console'

    class _Layer:
        def summary(self):
            return {'openai-codex': {'selected_method': 'oauth'}}

        def resolve(self, provider):
            if provider == 'openai-codex':
                return _Decision(provider, selected_method='oauth', runtime_confirmed=True)
            return _Decision(provider, selected_method='api_key', runtime_confirmed=True)

    optimizer = CostOptimizer({'routing_hierarchy': []})
    optimizer.configure_provider_access_layer(_Layer())
    report = BalanceReport(
        balances={
            'google': ProviderBalance(provider='google', has_credits=True, is_subscription=True, is_free_tier=True, source='estimated'),
        },
        providers_with_credits=['google'],
    )

    result = optimizer.optimize(
        model_preference='free',
        balance_report=report,
        available_models={
            'free': 'openrouter/google/gemini-2.5-flash:free',
            'heavy': 'openai/o3-pro',
        },
    )

    assert result.provider == 'google'
    assert result.model == 'google/gemini-2.5-flash'


def test_local_zero_marginal_path_is_selected_only_when_catalog_exposes_local_role():
    optimizer = CostOptimizer({'routing_hierarchy': []})

    result = optimizer.optimize(
        model_preference='local',
        balance_report=BalanceReport(),
        available_models={
            'local': 'local/qwen2.5-coder',
            'free': 'openrouter/google/gemini-2.5-flash:free',
        },
        exclude_models={'openrouter/google/gemini-2.5-flash:free'},
    )

    assert result.provider == 'local'
    assert result.model == 'local/qwen2.5-coder'
    assert result.access_method == 'local'


def test_local_runtime_does_not_override_general_free_route_when_other_credits_exist():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=3.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    result = optimizer.optimize(
        model_preference='free',
        balance_report=report,
        available_models={
            'local': 'lmstudio/qwen/qwen3-4b-thinking-2507',
            'free': 'deepseek/deepseek-chat',
            'heavy': 'openai/o3-pro',
        },
        estimated_tokens=128,
    )

    assert result.provider == 'deepseek'
    assert result.model == 'deepseek/deepseek-chat'


def test_local_runtime_can_be_selected_when_no_credits_exist():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 1.0,
                'speed_score': 72.0,
                'stability_score': 92.0,
                'capacity_status': 'capacity_ok',
                'prompt_type_suitability': {
                    'general_chat': 92.0,
                },
                'task_profiles': {
                    'general_chat': {'success': True},
                },
            }
        }
    })

    result = optimizer.optimize(
        model_preference='free',
        balance_report=BalanceReport(),
        available_models={
            'local': local_model,
            'free': 'openrouter/google/gemini-2.5-flash:free',
        },
        exclude_models={'openrouter/google/gemini-2.5-flash:free'},
        estimated_tokens=128,
    )

    assert result.provider == 'lmstudio'
    assert result.model == local_model


def test_local_profile_task_hint_does_not_promote_weak_general_chat_local_runtime():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 0.25,
                'speed_score': 46.0,
                'stability_score': 25.0,
                'capacity_status': 'capacity_estimate_conflict',
                'prompt_type_suitability': {
                    'general_chat': 10.0,
                    'reasoning': 20.0,
                    'coding': 100.0,
                    'structured_output': 15.0,
                },
                'task_profiles': {
                    'general_chat': {'success': False},
                    'reasoning': {'success': False},
                    'coding': {'success': True},
                    'structured_output': {'success': False},
                },
            }
        }
    })
    report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=3.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    result = optimizer.optimize(
        model_preference='free',
        balance_report=report,
        available_models={
            'local': local_model,
            'free': 'deepseek/deepseek-chat',
            'heavy': 'openai/o3-pro',
        },
        estimated_tokens=128,
        task_hint='casual_general_chat',
    )

    assert result.provider == 'deepseek'
    assert result.model == 'deepseek/deepseek-chat'


def test_local_profile_task_hint_can_promote_strong_coding_local_runtime():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 0.25,
                'speed_score': 46.0,
                'stability_score': 25.0,
                'capacity_status': 'capacity_estimate_conflict',
                'prompt_type_suitability': {
                    'general_chat': 10.0,
                    'reasoning': 20.0,
                    'coding': 100.0,
                    'structured_output': 15.0,
                },
                'task_profiles': {
                    'general_chat': {'success': False},
                    'reasoning': {'success': False},
                    'coding': {'success': True},
                    'structured_output': {'success': False},
                },
            }
        }
    })
    report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=3.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    result = optimizer.optimize(
        model_preference='heavy',
        balance_report=report,
        available_models={
            'local': local_model,
            'free': 'deepseek/deepseek-chat',
            'heavy': 'deepseek/deepseek-reasoner',
        },
        estimated_tokens=256,
        task_hint='heuristic_code_engineering',
    )

    assert result.provider == 'lmstudio'
    assert result.model == local_model
    assert result.access_method == 'local'


def test_runtime_confirmed_openai_codex_gpt54_beats_weak_local_coding_runtime():
    class _Decision:
        def __init__(self, provider, selected_method='disabled', runtime_confirmed=False):
            self.provider = provider
            self.selected_method = selected_method
            self.status = 'runtime_confirmed' if runtime_confirmed else 'disabled'
            self.reason = ''
            self.runtime_confirmed = runtime_confirmed
            self.target_form_reached = runtime_confirmed
            self.quota_visibility = 'provider_console'

    class _Layer:
        def summary(self):
            return {
                'openai-codex': {
                    'selected_method': 'oauth',
                    'runtime_confirmed': True,
                }
            }

        def resolve(self, provider):
            if provider == 'openai-codex':
                return _Decision(provider, selected_method='oauth', runtime_confirmed=True)
            return _Decision(provider, selected_method='api_key', runtime_confirmed=True)

    optimizer = CostOptimizer({'routing_hierarchy': []})
    optimizer.configure_provider_access_layer(_Layer())
    optimizer.configure_provider_capabilities({
        'openai-codex': {'openai-codex/gpt-5.4'},
        'deepseek': {'deepseek/deepseek-reasoner', 'deepseek/deepseek-chat'},
    })
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 0.25,
                'speed_score': 46.0,
                'stability_score': 25.0,
                'capacity_status': 'capacity_estimate_conflict',
                'prompt_type_suitability': {
                    'general_chat': 10.0,
                    'reasoning': 20.0,
                    'coding': 100.0,
                    'structured_output': 15.0,
                },
                'task_profiles': {
                    'general_chat': {'success': False},
                    'reasoning': {'success': False},
                    'coding': {'success': True},
                    'structured_output': {'success': False},
                },
            }
        }
    })
    report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=3.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    result = optimizer.optimize(
        model_preference='heavy',
        balance_report=report,
        available_models={
            'local': local_model,
            'free': 'deepseek/deepseek-chat',
            'heavy': 'deepseek/deepseek-reasoner',
        },
        estimated_tokens=256,
        task_hint='heuristic_code_engineering',
    )

    assert result.provider == 'openai-codex'
    assert result.model == 'openai-codex/gpt-5.4'
    assert result.access_method == 'oauth'
    assert result.reason == 'oauth_access:openai-codex'

def test_local_profile_reasoning_task_hint_keeps_cloud_heavy_route():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 0.25,
                'speed_score': 46.0,
                'stability_score': 25.0,
                'capacity_status': 'capacity_estimate_conflict',
                'prompt_type_suitability': {
                    'general_chat': 10.0,
                    'reasoning': 20.0,
                    'coding': 100.0,
                    'structured_output': 15.0,
                },
                'task_profiles': {
                    'general_chat': {'success': False},
                    'reasoning': {'success': False},
                    'coding': {'success': True},
                    'structured_output': {'success': False},
                },
            }
        }
    })
    report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=3.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    result = optimizer.optimize(
        model_preference='heavy',
        balance_report=report,
        available_models={
            'local': local_model,
            'free': 'deepseek/deepseek-chat',
            'heavy': 'deepseek/deepseek-reasoner',
        },
        estimated_tokens=256,
        task_hint='deep_reasoning_analysis',
    )

    assert result.provider == 'deepseek'
    assert result.model != local_model


def test_local_profile_structured_output_keeps_cloud_route_even_without_other_credits():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 0.25,
                'speed_score': 46.0,
                'stability_score': 25.0,
                'capacity_status': 'capacity_estimate_conflict',
                'prompt_type_suitability': {
                    'general_chat': 10.0,
                    'reasoning': 20.0,
                    'coding': 100.0,
                    'structured_output': 15.0,
                },
                'task_profiles': {
                    'general_chat': {'success': False},
                    'reasoning': {'success': False},
                    'coding': {'success': True},
                    'structured_output': {'success': False},
                },
            }
        }
    })

    result = optimizer.optimize(
        model_preference='free',
        balance_report=BalanceReport(),
        available_models={
            'local': local_model,
            'free': 'openrouter/google/gemini-2.5-flash:free',
        },
        estimated_tokens=128,
        task_hint='return_structured_json_schema_only',
    )

    assert result.provider == 'openrouter'
    assert result.model == 'openrouter/google/gemini-2.5-flash:free'


def test_runtime_confirmed_openai_codex_does_not_override_general_chat_free_route():
    class _Decision:
        def __init__(self, provider, selected_method='disabled', runtime_confirmed=False):
            self.provider = provider
            self.selected_method = selected_method
            self.status = 'runtime_confirmed' if runtime_confirmed else 'disabled'
            self.reason = ''
            self.runtime_confirmed = runtime_confirmed
            self.target_form_reached = runtime_confirmed
            self.quota_visibility = 'provider_console'

    class _Layer:
        def summary(self):
            return {
                'openai-codex': {
                    'selected_method': 'oauth',
                    'runtime_confirmed': True,
                }
            }

        def resolve(self, provider):
            if provider == 'openai-codex':
                return _Decision(provider, selected_method='oauth', runtime_confirmed=True)
            return _Decision(provider, selected_method='api_key', runtime_confirmed=True)

    optimizer = CostOptimizer({'routing_hierarchy': []})
    optimizer.configure_provider_access_layer(_Layer())
    optimizer.configure_provider_capabilities({
        'openai-codex': {'openai-codex/gpt-5.4'},
        'deepseek': {'deepseek/deepseek-chat'},
    })
    report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=3.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    result = optimizer.optimize(
        model_preference='free',
        balance_report=report,
        available_models={
            'free': 'deepseek/deepseek-chat',
            'heavy': 'deepseek/deepseek-reasoner',
        },
        estimated_tokens=64,
        task_hint='casual_general_chat',
    )

    assert result.provider == 'deepseek'
    assert result.model == 'deepseek/deepseek-chat'


def test_runtime_confirmed_openai_codex_does_not_override_simple_structured_route():
    class _Decision:
        def __init__(self, provider, selected_method='disabled', runtime_confirmed=False):
            self.provider = provider
            self.selected_method = selected_method
            self.status = 'runtime_confirmed' if runtime_confirmed else 'disabled'
            self.reason = ''
            self.runtime_confirmed = runtime_confirmed
            self.target_form_reached = runtime_confirmed
            self.quota_visibility = 'provider_console'

    class _Layer:
        def summary(self):
            return {
                'openai-codex': {
                    'selected_method': 'oauth',
                    'runtime_confirmed': True,
                }
            }

        def resolve(self, provider):
            if provider == 'openai-codex':
                return _Decision(provider, selected_method='oauth', runtime_confirmed=True)
            return _Decision(provider, selected_method='api_key', runtime_confirmed=True)

    optimizer = CostOptimizer({'routing_hierarchy': []})
    optimizer.configure_provider_access_layer(_Layer())
    optimizer.configure_provider_capabilities({
        'openai-codex': {'openai-codex/gpt-5.4'},
        'deepseek': {'deepseek/deepseek-chat'},
    })
    report = BalanceReport(
        balances={
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=3.0, source='api'),
        },
        providers_with_credits=['deepseek'],
    )

    result = optimizer.optimize(
        model_preference='free',
        balance_report=report,
        available_models={
            'free': 'deepseek/deepseek-chat',
            'heavy': 'deepseek/deepseek-reasoner',
        },
        estimated_tokens=64,
        task_hint='return_structured_json_schema_only',
    )

    assert result.provider == 'deepseek'
    assert result.model == 'deepseek/deepseek-chat'
