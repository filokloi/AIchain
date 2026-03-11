#!/usr/bin/env python3
"""Tests for aichaind.providers.adapters.local_openai."""

from types import SimpleNamespace

from aichaind.providers.base import CompletionRequest
from aichaind.providers.adapters.local_openai import LocalOpenAIAdapter


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class TestLocalOpenAIAdapter:
    def test_format_model_id_strips_local_prefix(self):
        adapter = LocalOpenAIAdapter("local", base_url="http://127.0.0.1:11434/v1")
        assert adapter.format_model_id("local/qwen2.5-coder") == "qwen2.5-coder"

    def test_discover_success_without_api_key(self, monkeypatch):
        def fake_get(url, headers=None, timeout=None):
            return FakeResponse(200, {"data": [{"id": "qwen2.5-coder"}]})

        monkeypatch.setattr(
            "aichaind.providers.adapters.local_openai.requests",
            SimpleNamespace(get=fake_get, post=lambda *args, **kwargs: FakeResponse(200), Timeout=TimeoutError),
        )
        adapter = LocalOpenAIAdapter("local", base_url="http://127.0.0.1:11434/v1")
        result = adapter.discover()

        assert result.status == "authenticated"
        assert result.available_models == ["local/qwen2.5-coder"]

    def test_execute_success(self, monkeypatch):
        def fake_post(url, headers=None, json=None, timeout=None):
            return FakeResponse(200, {
                "model": "qwen2.5-coder",
                "choices": [{"message": {"content": "local ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 6},
            })

        monkeypatch.setattr(
            "aichaind.providers.adapters.local_openai.requests",
            SimpleNamespace(get=lambda *args, **kwargs: FakeResponse(200), post=fake_post, Timeout=TimeoutError),
        )
        adapter = LocalOpenAIAdapter("local", base_url="http://127.0.0.1:11434/v1")
        request = CompletionRequest(
            model="local/qwen2.5-coder",
            messages=[{"role": "user", "content": "hello"}],
        )

        response = adapter.execute(request)

        assert response.status == "success"
        assert response.content == "local ok"
        assert response.input_tokens == 10
        assert response.output_tokens == 6

    def test_execute_uses_profile_timeout_override(self, monkeypatch):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["timeout"] = timeout
            return FakeResponse(200, {
                "model": "qwen2.5-coder",
                "choices": [{"message": {"content": "local ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 6},
            })

        monkeypatch.setattr(
            "aichaind.providers.adapters.local_openai.requests",
            SimpleNamespace(get=lambda *args, **kwargs: FakeResponse(200), post=fake_post, Timeout=TimeoutError),
        )
        adapter = LocalOpenAIAdapter("local", base_url="http://127.0.0.1:11434/v1")
        request = CompletionRequest(
            model="local/qwen2.5-coder",
            messages=[{"role": "user", "content": "hello"}],
            extra={"timeout_ms": 90000},
        )

        response = adapter.execute(request)

        assert response.status == "success"
        assert captured["timeout"] == 90.0

    def test_health_check_false_on_error(self, monkeypatch):
        def fake_get(url, headers=None, timeout=None):
            raise RuntimeError("offline")

        monkeypatch.setattr(
            "aichaind.providers.adapters.local_openai.requests",
            SimpleNamespace(get=fake_get, post=lambda *args, **kwargs: FakeResponse(200), Timeout=TimeoutError),
        )
        adapter = LocalOpenAIAdapter("local", base_url="http://127.0.0.1:11434/v1")
        assert adapter.health_check() is False
