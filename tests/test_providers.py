#!/usr/bin/env python3
"""
Tests for aichaind.providers — base, registry, adapters (unit tests only, no network)

Covers:
- ProviderAdapter ABC enforcement
- ProviderCircuitBreaker state transitions
- Provider registry factory
- Adapter format_model_id
- A2A stub behavior
"""

import pytest
from aichaind.providers.base import (
    ProviderAdapter, ProviderCircuitBreaker,
    CompletionRequest, CompletionResponse, DiscoveryResult,
)
from aichaind.providers.registry import (
    get_adapter, get_adapter_for_model, list_providers, discover_all,
)
from aichaind.providers.adapters.a2a import A2AAdapter


class TestProviderCircuitBreaker:
    def test_starts_closed(self):
        cb = ProviderCircuitBreaker()
        assert cb.state == "CLOSED"
        assert cb.is_available is True

    def test_opens_after_threshold(self):
        cb = ProviderCircuitBreaker(failure_threshold=3, reset_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "CLOSED"
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.is_available is False

    def test_success_resets(self):
        cb = ProviderCircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == "CLOSED"
        assert cb._failures == 0

    def test_half_open_after_timeout(self):
        cb = ProviderCircuitBreaker(failure_threshold=1, reset_timeout=0.1)
        cb.record_failure()
        assert cb.state == "OPEN"
        import time
        time.sleep(0.15)
        assert cb.state == "HALF_OPEN"
        assert cb.is_available is True


class TestProviderRegistry:
    def test_list_providers_has_defaults(self):
        providers = list_providers()
        assert "openrouter" in providers
        assert "google" in providers
        assert "groq" in providers

    def test_get_adapter_openrouter(self):
        adapter = get_adapter("openrouter")
        assert adapter is not None
        assert adapter.name == "openrouter"

    def test_get_adapter_for_model_google(self):
        adapter = get_adapter_for_model("google/gemini-2.5-pro")
        assert adapter is not None
        assert adapter.name == "google"

    def test_get_adapter_unknown_falls_back(self):
        adapter = get_adapter_for_model("unknown-provider/some-model")
        assert adapter is not None
        # Should fall back to openrouter
        assert adapter.name == "openrouter"

    def test_format_model_id_strips_own_prefix(self):
        adapter = get_adapter("google")
        assert adapter.format_model_id("google/gemini-2.5-pro") == "gemini-2.5-pro"

    def test_format_model_id_keeps_other_prefix(self):
        adapter = get_adapter("google")
        assert adapter.format_model_id("openai/gpt-4o") == "openai/gpt-4o"


class TestA2AStub:
    def test_a2a_discovery_unconfigured(self):
        a2a = A2AAdapter()
        result = a2a.discover()
        assert result.status == "unconfigured"
        assert result.available_models == []

    def test_a2a_execute_returns_error(self):
        a2a = A2AAdapter()
        req = CompletionRequest(model="agent/specialist", messages=[])
        resp = a2a.execute(req)
        assert resp.status == "error"
        assert "not yet available" in resp.error

    def test_a2a_health_check_false(self):
        a2a = A2AAdapter()
        assert a2a.health_check() is False


class TestCompletionContracts:
    def test_completion_request_defaults(self):
        req = CompletionRequest(model="test", messages=[])
        assert req.max_tokens == 4096
        assert req.temperature == 0.7
        assert req.stream is False

    def test_completion_response_defaults(self):
        resp = CompletionResponse(model="test", content="hello")
        assert resp.status == "success"
        assert resp.content == "hello"
        assert resp.error == ""

    def test_discovery_result_defaults(self):
        dr = DiscoveryResult()
        assert dr.status == "unconfigured"
        assert dr.available_models == []
