#!/usr/bin/env python3
"""Tests for local runtime detection and resolution."""

from types import SimpleNamespace

from aichaind.providers.local_runtime import (
    LocalRuntimeProbe,
    choose_preferred_local_model,
    iter_local_model_candidates,
    normalize_local_model,
    probe_local_completion,
    resolve_local_execution,
    select_best_local_runtime,
)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_resolve_local_execution_uses_discovered_model_for_configured_provider(monkeypatch):
    def fake_detect(providers, timeout=2.5, base_url_overrides=None):
        assert providers == ["local"]
        assert base_url_overrides == {"local": "http://127.0.0.1:11434/v1"}
        return [
            LocalRuntimeProbe(
                provider="local",
                base_url="http://127.0.0.1:11434/v1",
                reachable=True,
                discovered_models=["local/qwen2.5-coder"],
                health_checked=True,
            )
        ]

    monkeypatch.setattr("aichaind.providers.local_runtime.detect_local_runtimes", fake_detect)

    resolution = resolve_local_execution(
        {
            "enabled": True,
            "provider": "local",
            "base_url": "http://127.0.0.1:11434/v1",
            "default_model": "",
            "auto_detect": False,
        }
    )

    assert resolution.status == "runtime_confirmed"
    assert resolution.provider == "local"
    assert resolution.model == "local/qwen2.5-coder"
    assert resolution.health_check_ok is True



def test_resolve_local_execution_honors_explicit_base_url_override_for_configured_provider(monkeypatch):
    captured = {}

    def fake_detect(providers, timeout=2.5, base_url_overrides=None):
        captured["providers"] = providers
        captured["base_url_overrides"] = base_url_overrides
        return [
            LocalRuntimeProbe(
                provider="local",
                base_url=base_url_overrides["local"],
                reachable=True,
                discovered_models=["local/qwen-local-mock"],
                health_checked=True,
            )
        ]

    monkeypatch.setattr("aichaind.providers.local_runtime.detect_local_runtimes", fake_detect)

    resolution = resolve_local_execution(
        {
            "enabled": True,
            "provider": "local",
            "base_url": "http://127.0.0.1:64933/v1",
            "default_model": "local/qwen-local-mock",
            "auto_detect": False,
        }
    )

    assert captured["providers"] == ["local"]
    assert captured["base_url_overrides"] == {"local": "http://127.0.0.1:64933/v1"}
    assert resolution.base_url == "http://127.0.0.1:64933/v1"
    assert resolution.status == "runtime_confirmed"


def test_resolve_local_execution_auto_detects_fallback_provider(monkeypatch):
    def fake_detect(providers, timeout=2.5, base_url_overrides=None):
        assert providers == ["local", "ollama", "lmstudio"]
        assert base_url_overrides == {"local": "http://127.0.0.1:11434/v1"}
        return [
            LocalRuntimeProbe(
                provider="local",
                base_url="http://127.0.0.1:11434/v1",
                reachable=False,
                discovered_models=[],
                health_checked=True,
                error="connection refused",
            ),
            LocalRuntimeProbe(
                provider="ollama",
                base_url="http://127.0.0.1:11434/v1",
                reachable=False,
                discovered_models=[],
                health_checked=True,
                error="no models",
            ),
            LocalRuntimeProbe(
                provider="lmstudio",
                base_url="http://127.0.0.1:1234/v1",
                reachable=True,
                discovered_models=["lmstudio/qwen2.5-coder", "lmstudio/gemma-3"],
                health_checked=True,
            ),
        ]

    monkeypatch.setattr("aichaind.providers.local_runtime.detect_local_runtimes", fake_detect)

    resolution = resolve_local_execution(
        {
            "enabled": True,
            "provider": "local",
            "base_url": "http://127.0.0.1:11434/v1",
            "default_model": "local/qwen2.5-coder",
            "auto_detect": True,
            "preferred_providers": ["ollama", "lmstudio", "local"],
        }
    )

    assert resolution.status == "runtime_confirmed"
    assert resolution.provider == "lmstudio"
    assert resolution.base_url == "http://127.0.0.1:1234/v1"
    assert resolution.model == "lmstudio/qwen2.5-coder"



def test_resolve_local_execution_disabled_still_reports_detected_runtimes(monkeypatch):
    def fake_detect(providers, timeout=2.5, base_url_overrides=None):
        assert base_url_overrides == {"local": "http://127.0.0.1:11434/v1"}
        return [
            LocalRuntimeProbe(
                provider="lmstudio",
                base_url="http://127.0.0.1:1234/v1",
                reachable=True,
                discovered_models=["lmstudio/qwen2.5-coder"],
                executable_present=True,
                executable_path="C:/Users/filok/.lmstudio/bin/lms.exe",
                health_checked=True,
            )
        ]

    monkeypatch.setattr("aichaind.providers.local_runtime.detect_local_runtimes", fake_detect)

    resolution = resolve_local_execution(
        {
            "enabled": False,
            "provider": "local",
            "base_url": "http://127.0.0.1:11434/v1",
            "default_model": "",
            "auto_detect": True,
            "preferred_providers": ["lmstudio", "local"],
        },
        detect_when_disabled=True,
    )

    assert resolution.status == "disabled"
    assert len(resolution.probes) == 1
    assert resolution.probes[0].provider == "lmstudio"
    assert resolution.probes[0].reachable is True



def test_select_best_local_runtime_prefers_requested_model_and_provider_order():
    probes = [
        LocalRuntimeProbe(
            provider="ollama",
            base_url="http://127.0.0.1:11434/v1",
            reachable=True,
            discovered_models=["ollama/llama3.2", "ollama/deepseek-r1"],
        ),
        LocalRuntimeProbe(
            provider="lmstudio",
            base_url="http://127.0.0.1:1234/v1",
            reachable=True,
            discovered_models=["lmstudio/qwen2.5-coder", "lmstudio/gemma-3"],
        ),
    ]

    selected = select_best_local_runtime(
        probes,
        preferred_providers=["ollama", "lmstudio"],
        requested_model="qwen2.5-coder",
    )

    assert selected is not None
    assert selected.provider == "lmstudio"



def test_choose_preferred_local_model_skips_embedding_models_by_default():
    models = [
        "lmstudio/text-embedding-nomic-embed-text-v1.5",
        "lmstudio/qwen/qwen3.5-9b",
    ]

    assert choose_preferred_local_model(models) == "lmstudio/qwen/qwen3.5-9b"



def test_iter_local_model_candidates_falls_back_from_stale_requested_model():
    candidates = iter_local_model_candidates(
        "lmstudio",
        [
            "lmstudio/google/gemma-3-4b",
            "lmstudio/qwen/qwen3-4b-thinking-2507",
            "lmstudio/text-embedding-nomic-embed-text-v1.5",
        ],
        requested_model="lmstudio/qwen3-4b-local",
        resolved_model="lmstudio/qwen3-4b-local",
    )

    assert candidates == [
        "lmstudio/qwen3-4b-local",
        "lmstudio/google/gemma-3-4b",
        "lmstudio/qwen/qwen3-4b-thinking-2507",
    ]



def test_probe_local_completion_succeeds_with_minimal_chat(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert url == "http://127.0.0.1:1234/v1/chat/completions"
        assert json["model"] == "qwen/qwen3.5-9b"
        return _FakeResponse(200, {
            "choices": [{"message": {"content": "OK"}}]
        })

    monkeypatch.setattr(
        "aichaind.providers.local_runtime.requests",
        SimpleNamespace(post=fake_post),
    )

    ok, detail = probe_local_completion(
        "lmstudio",
        "lmstudio/qwen/qwen3.5-9b",
        base_url="http://127.0.0.1:1234/v1",
        timeout=1.0,
    )

    assert ok is True
    assert detail == "OK"



def test_normalize_local_model_rewrites_local_prefix_for_selected_provider():
    assert normalize_local_model("local/qwen2.5-coder", "lmstudio") == "lmstudio/qwen2.5-coder"
    assert normalize_local_model("qwen2.5-coder", "ollama") == "ollama/qwen2.5-coder"


def test_resolve_local_execution_replaces_stale_model_with_discovered_model(monkeypatch):
    def fake_detect(providers, timeout=2.5, base_url_overrides=None):
        return [
            LocalRuntimeProbe(
                provider="lmstudio",
                base_url="http://127.0.0.1:1234/v1",
                reachable=True,
                discovered_models=[
                    "lmstudio/google/gemma-3-4b",
                    "lmstudio/qwen/qwen3-4b-thinking-2507",
                ],
                health_checked=True,
            )
        ]

    monkeypatch.setattr("aichaind.providers.local_runtime.detect_local_runtimes", fake_detect)

    resolution = resolve_local_execution(
        {
            "enabled": True,
            "provider": "lmstudio",
            "base_url": "http://127.0.0.1:1234/v1",
            "default_model": "lmstudio/qwen3-4b-local",
            "auto_detect": False,
        }
    )

    assert resolution.status == "runtime_confirmed"
    assert resolution.model == "lmstudio/google/gemma-3-4b"
