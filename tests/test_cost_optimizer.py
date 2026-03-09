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
