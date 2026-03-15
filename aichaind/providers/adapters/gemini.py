#!/usr/bin/env python3
"""
aichaind.providers.adapters.gemini — Google Gemini Provider Adapter

Refactored from ai-chain-skill/providers/gemini.py.
Now implements full ProviderAdapter contract.
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

log = logging.getLogger("aichaind.providers.gemini")

# Gemini OpenAI-compatible endpoint
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiAdapter(ProviderAdapter):
    """Google Gemini direct API adapter."""

    def __init__(self, api_key: str = ""):
        key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
        super().__init__(name="google", api_key=key)

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        if not requests or not self.api_key:
            if not self.api_key:
                log.info("Gemini discovery skipped (no API key)")
            else:
                result.status = "error"
            return result

        try:
            url = f"{MODELS_ENDPOINT}?key={self.api_key}"
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                result.status = "auth_failed"
                return result

            result.status = "authenticated"
            models_data = resp.json().get("models", [])
            result.available_models = [m["name"].replace("models/", "google/") for m in models_data]
            log.info(f"Gemini: {len(result.available_models)} models discovered")

        except Exception as e:
            log.error(f"Gemini discovery error: {e}")
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
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        start_t = time.time()
        try:
            resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=self.resolve_timeout(request, default=45.0, max_timeout=120.0))
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
            resp = requests.get(f"{MODELS_ENDPOINT}?key={self.api_key}", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
