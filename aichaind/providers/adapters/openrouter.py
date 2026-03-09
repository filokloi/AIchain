#!/usr/bin/env python3
"""
aichaind.providers.adapters.openrouter — OpenRouter Provider Adapter

Refactored from ai-chain-skill/providers/openrouter.py.
Now implements full ProviderAdapter contract: discover + execute + health.
"""

import os
import time
import logging

try:
    import requests
except ImportError:
    requests = None

from aichaind.providers.base import (
    ProviderAdapter, CompletionRequest, CompletionResponse, DiscoveryResult
)

log = logging.getLogger("aichaind.providers.openrouter")

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
AUTH_ENDPOINT = "https://openrouter.ai/api/v1/auth/key"
MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"


class OpenRouterAdapter(ProviderAdapter):
    """OpenRouter provider adapter — the universal fallback."""

    def __init__(self, api_key: str = ""):
        key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY") or ""
        super().__init__(name="openrouter", api_key=key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/filok94/AIchain",
            "X-Title": "AIchain aichaind",
        }

    def discover(self) -> DiscoveryResult:
        """Discover capabilities from OpenRouter API."""
        result = DiscoveryResult()
        if not requests:
            result.status = "error"
            return result
        if not self.api_key:
            log.info("OpenRouter discovery skipped (no API key)")
            return result

        try:
            # Auth check
            auth_resp = requests.get(AUTH_ENDPOINT, headers=self._headers(), timeout=15)
            if auth_resp.status_code != 200:
                result.status = "auth_failed"
                return result

            auth_data = auth_resp.json().get("data", {})
            result.limits = {
                "credit_limit": auth_data.get("limit"),
                "credit_usage": auth_data.get("usage"),
                "credit_remaining": auth_data.get("limit_remaining"),
                "is_free_tier": auth_data.get("is_free_tier", False),
            }
            result.status = "authenticated"

            # Models
            models_resp = requests.get(MODELS_ENDPOINT, headers=self._headers(), timeout=15)
            if models_resp.status_code == 200:
                models_data = models_resp.json().get("data", [])
                result.available_models = [m["id"] for m in models_data]
                log.info(f"OpenRouter: {len(result.available_models)} models discovered")

        except Exception as e:
            log.error(f"OpenRouter discovery error: {e}")
            result.status = "error"

        return result

    def execute(self, request: CompletionRequest) -> CompletionResponse:
        """Execute a completion via OpenRouter."""
        if not requests:
            return CompletionResponse(model=request.model, content="", error="requests not installed", status="error")

        if not self.circuit_breaker.is_available:
            return CompletionResponse(model=request.model, content="", error="circuit breaker open", status="error")

        payload = {
            "model": request.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        start_t = time.time()
        try:
            resp = requests.post(ENDPOINT, json=payload, headers=self._headers(), timeout=60)
            latency = (time.time() - start_t) * 1000

            if resp.status_code != 200:
                self.circuit_breaker.record_failure()
                return CompletionResponse(
                    model=request.model, content="",
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    status="error", latency_ms=latency,
                )

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            usage = data.get("usage", {})

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
            return CompletionResponse(model=request.model, content="", error="timeout", status="timeout",
                                      latency_ms=(time.time() - start_t) * 1000)
        except Exception as e:
            self.circuit_breaker.record_failure()
            return CompletionResponse(model=request.model, content="", error=str(e), status="error",
                                      latency_ms=(time.time() - start_t) * 1000)

    def health_check(self) -> bool:
        if not requests or not self.api_key:
            return False
        try:
            resp = requests.get(AUTH_ENDPOINT, headers=self._headers(), timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def supports_streaming(self) -> bool:
        return True
