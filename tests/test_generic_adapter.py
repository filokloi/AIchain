#!/usr/bin/env python3
"""Tests for aichaind.providers.adapters.generic."""

from types import SimpleNamespace

from aichaind.providers.base import CompletionRequest
from aichaind.providers.adapters.generic import GenericOpenAIAdapter


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class TestGenericOpenAIAdapter:
    def test_unconfigured_discovery_without_key(self, monkeypatch):
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        adapter = GenericOpenAIAdapter("mistral")
        result = adapter.discover()
        assert result.status == "unconfigured"
        assert result.available_models == []

    def test_format_model_id_strips_prefix(self):
        adapter = GenericOpenAIAdapter("mistral", api_key="key")
        assert adapter.format_model_id("mistral/mistral-large") == "mistral-large"

    def test_format_model_id_normalizes_openrouter_aliases_for_direct_providers(self):
        adapter = GenericOpenAIAdapter("xai", api_key="key")
        assert adapter.format_model_id("openrouter/xai/grok-3-mini:free") == "grok-3-mini"

    def test_execute_success(self, monkeypatch):
        def fake_post(url, headers=None, json=None, timeout=None):
            return FakeResponse(200, {
                "model": "mistral-large",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            })

        monkeypatch.setattr(
            "aichaind.providers.adapters.generic.requests",
            SimpleNamespace(post=fake_post, get=lambda *args, **kwargs: FakeResponse(200), Timeout=TimeoutError),
        )
        adapter = GenericOpenAIAdapter("mistral", api_key="key")
        request = CompletionRequest(
            model=adapter.format_model_id("mistral/mistral-large"),
            messages=[{"role": "user", "content": "hello"}],
        )

        response = adapter.execute(request)

        assert response.status == "success"
        assert response.content == "ok"
        assert response.model == "mistral-large"
        assert response.input_tokens == 12
        assert response.output_tokens == 8

    def test_health_check_uses_models_endpoint(self, monkeypatch):
        calls = []

        def fake_get(url, headers=None, timeout=None):
            calls.append(url)
            return FakeResponse(200, {"data": [{"id": "mistral-large"}]})

        monkeypatch.setattr(
            "aichaind.providers.adapters.generic.requests",
            SimpleNamespace(post=lambda *args, **kwargs: FakeResponse(200), get=fake_get, Timeout=TimeoutError),
        )
        adapter = GenericOpenAIAdapter("mistral", api_key="key")

        assert adapter.health_check() is True
        assert calls[0].endswith("/models")
