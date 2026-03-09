#!/usr/bin/env python3
"""
aichaind.providers.adapters.openai_native — OpenAI Direct API Adapter

Direct OpenAI API (not via OpenRouter).
Implements full ProviderAdapter contract.
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

log = logging.getLogger("aichaind.providers.openai_native")

ENDPOINT = "https://api.openai.com/v1/chat/completions"
MODELS_ENDPOINT = "https://api.openai.com/v1/models"


class OpenAINativeAdapter(ProviderAdapter):
    """OpenAI direct API adapter."""

    def __init__(self, api_key: str = ""):
        key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY") or ""
        super().__init__(name="openai", api_key=key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        if not requests or not self.api_key:
            if not self.api_key:
                log.info("OpenAI discovery skipped (no API key)")
            return result

        try:
            resp = requests.get(MODELS_ENDPOINT, headers=self._headers(), timeout=15)
            if resp.status_code != 200:
                result.status = "auth_failed"
                return result

            result.status = "authenticated"
            models_data = resp.json().get("data", [])
            # Filter to chat-capable models
            chat_models = [m for m in models_data if any(
                prefix in m["id"] for prefix in ("gpt-", "o1", "o3", "o4")
            )]
            result.available_models = [f"openai/{m['id']}" for m in chat_models]
            result.cost_mode = "api-per-token"
            log.info(f"OpenAI: {len(result.available_models)} chat models discovered")

        except Exception as e:
            log.error(f"OpenAI discovery error: {e}")
            result.status = "error"
        return result

    def execute(self, request: CompletionRequest) -> CompletionResponse:
        if not requests:
            return CompletionResponse(model=request.model, content="", error="requests not installed", status="error")
        if not self.circuit_breaker.is_available:
            return CompletionResponse(model=request.model, content="", error="circuit breaker open", status="error")

        model_name = self.format_model_id(request.model)
        payload = {
            "model": model_name,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False,
        }

        start_t = time.time()
        try:
            resp = requests.post(ENDPOINT, json=payload, headers=self._headers(), timeout=90)
            latency = (time.time() - start_t) * 1000

            if resp.status_code != 200:
                self.circuit_breaker.record_failure()
                return CompletionResponse(model=request.model, content="",
                                          error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                                          status="error", latency_ms=latency)

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
                latency_ms=latency, raw_response=data, status="success",
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
            resp = requests.get(MODELS_ENDPOINT, headers=self._headers(), timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def supports_streaming(self) -> bool:
        return True
