#!/usr/bin/env python3
"""
aichaind.providers.adapters.local_openai — Local OpenAI-Compatible Adapter

Supports local runtimes that expose an OpenAI-compatible API surface, such as:
- local generic gateways
- vLLM
- Ollama (OpenAI-compatible mode)
- LM Studio
- llama.cpp servers exposing /v1

This adapter is intentionally auth-optional and local-first. It does not assume
cloud credentials and is safe to use as a policy-driven privacy fallback.
"""

import os
import time
import logging

try:
    import requests
except ImportError:
    requests = None

from aichaind.providers.base import (
    ProviderAdapter, CompletionRequest, CompletionResponse, DiscoveryResult,
)

log = logging.getLogger("aichaind.providers.local_openai")


LOCAL_PROVIDER_CONFIG = {
    "local": {
        "env_keys": ["AICHAIN_LOCAL_BASE_URL", "LOCAL_LLM_BASE_URL"],
        "api_key_env": ["AICHAIN_LOCAL_API_KEY", "LOCAL_LLM_API_KEY"],
        "default_base_url": "http://127.0.0.1:11434/v1",
    },
    "vllm": {
        "env_keys": ["VLLM_BASE_URL", "AICHAIN_LOCAL_BASE_URL", "LOCAL_LLM_BASE_URL"],
        "api_key_env": ["VLLM_API_KEY", "AICHAIN_LOCAL_API_KEY", "LOCAL_LLM_API_KEY"],
        "default_base_url": "http://127.0.0.1:8000/v1",
    },
    "ollama": {
        "env_keys": ["OLLAMA_BASE_URL", "AICHAIN_LOCAL_BASE_URL", "LOCAL_LLM_BASE_URL"],
        "api_key_env": ["OLLAMA_API_KEY", "AICHAIN_LOCAL_API_KEY", "LOCAL_LLM_API_KEY"],
        "default_base_url": "http://127.0.0.1:11434/v1",
    },
    "lmstudio": {
        "env_keys": ["LMSTUDIO_BASE_URL", "AICHAIN_LOCAL_BASE_URL", "LOCAL_LLM_BASE_URL"],
        "api_key_env": ["LMSTUDIO_API_KEY", "AICHAIN_LOCAL_API_KEY", "LOCAL_LLM_API_KEY"],
        "default_base_url": "http://127.0.0.1:1234/v1",
    },
    "llamacpp": {
        "env_keys": ["LLAMACPP_BASE_URL", "AICHAIN_LOCAL_BASE_URL", "LOCAL_LLM_BASE_URL"],
        "api_key_env": ["LLAMACPP_API_KEY", "AICHAIN_LOCAL_API_KEY", "LOCAL_LLM_API_KEY"],
        "default_base_url": "http://127.0.0.1:8080/v1",
    },
}

_LOCAL_ALIASES = frozenset(LOCAL_PROVIDER_CONFIG.keys())


class LocalOpenAIAdapter(ProviderAdapter):
    """Adapter for local OpenAI-compatible runtimes."""

    def __init__(self, provider_name: str = "local", base_url: str = "", api_key: str = ""):
        self.provider_name = (provider_name or "local").lower()
        config = LOCAL_PROVIDER_CONFIG.get(self.provider_name, LOCAL_PROVIDER_CONFIG["local"])
        self.base_url = base_url or _first_env(config["env_keys"]) or config["default_base_url"]
        resolved_key = api_key or _first_env(config["api_key_env"]) or ""
        # Local runtimes on low-memory machines can be materially slower than cloud APIs.
        # Keep a wider timeout so privacy/local-only routes complete instead of failing early.
        self._timeout = 60
        super().__init__(name=self.provider_name, api_key=resolved_key, access_methods={"local"})

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _resolve_timeout(self, request: CompletionRequest) -> float:
        timeout_ms = None
        if isinstance(getattr(request, "extra", None), dict):
            timeout_ms = request.extra.get("timeout_ms")
        try:
            timeout_ms = float(timeout_ms)
        except (TypeError, ValueError):
            timeout_ms = None
        if timeout_ms is None:
            return float(self._timeout)
        return max(20.0, min(timeout_ms / 1000.0, 180.0))

    def format_model_id(self, model_id: str) -> str:
        if "/" not in model_id:
            return model_id
        prefix, rest = model_id.split("/", 1)
        if prefix.lower() in _LOCAL_ALIASES:
            return rest
        return model_id

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        if not requests or not self.base_url:
            return result

        try:
            resp = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=8)
            if resp.status_code != 200:
                result.status = "error"
                return result

            data = resp.json().get("data", [])
            result.status = "authenticated"
            result.available_models = [
                f"{self.provider_name}/{item['id']}"
                for item in data
                if isinstance(item, dict) and item.get("id")
            ]
            result.cost_mode = "local-fixed"
            return result
        except Exception as exc:
            log.warning(f"Local discovery error ({self.provider_name}): {exc}")
            result.status = "error"
            return result

    def execute(self, request: CompletionRequest) -> CompletionResponse:
        if not requests:
            return CompletionResponse(model=request.model, content="", error="requests not installed", status="error")
        if not self.base_url:
            return CompletionResponse(model=request.model, content="", error="local base_url not configured", status="error")
        if not self.circuit_breaker.is_available:
            return CompletionResponse(model=request.model, content="", error="circuit breaker open", status="error")

        payload = {
            "model": self.format_model_id(request.model),
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False,
        }

        start_t = time.time()
        timeout_s = self._resolve_timeout(request)
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=timeout_s,
            )
            latency = (time.time() - start_t) * 1000
            if resp.status_code != 200:
                self.circuit_breaker.record_failure()
                return CompletionResponse(
                    model=request.model,
                    content="",
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    status="error",
                    latency_ms=latency,
                )

            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            usage = data.get("usage") or {}
            self.circuit_breaker.record_success()
            return CompletionResponse(
                model=request.model,
                content=choice.get("message", {}).get("content", ""),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                finish_reason=choice.get("finish_reason", ""),
                latency_ms=latency,
                raw_response=data,
                status="success",
            )
        except requests.Timeout:
            self.circuit_breaker.record_failure()
            return CompletionResponse(
                model=request.model,
                content="",
                error="timeout",
                status="timeout",
                latency_ms=(time.time() - start_t) * 1000,
            )
        except Exception as exc:
            self.circuit_breaker.record_failure()
            return CompletionResponse(
                model=request.model,
                content="",
                error=str(exc),
                status="error",
                latency_ms=(time.time() - start_t) * 1000,
            )

    def health_check(self) -> bool:
        if not requests or not self.base_url:
            return False
        try:
            resp = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def supports_streaming(self) -> bool:
        return True


def _first_env(keys: list[str]) -> str:
    for key in keys:
        value = os.environ.get(key, "")
        if value:
            return value
    return ""

