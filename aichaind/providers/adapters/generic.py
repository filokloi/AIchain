#!/usr/bin/env python3
"""
aichaind.providers.adapters.generic — Generic OpenAI-Compatible Adapter

Universal adapter for any provider using OpenAI-compatible API.
Works with: Mistral, XAI/Grok, Cohere Command-R, Moonshot, Zhipu, etc.

Config:
  - base_url: provider API endpoint
  - api_key: from env var
  - model_prefix: for model ID resolution
"""

import os
import time
import logging
import requests

from aichaind.providers.base import (
    ProviderAdapter, CompletionRequest, CompletionResponse, DiscoveryResult,
)

log = logging.getLogger("aichaind.providers.adapters.generic")


# Known OpenAI-compatible providers and their base URLs
KNOWN_PROVIDERS = {
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "strip_prefix": True,
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "env_key": "XAI_API_KEY",
        "strip_prefix": True,
    },
    "cohere": {
        "base_url": "https://api.cohere.com/v2",
        "env_key": "COHERE_API_KEY",
        "strip_prefix": True,
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "strip_prefix": True,
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "ZHIPU_API_KEY",
        "strip_prefix": True,
    },
}


class GenericOpenAIAdapter(ProviderAdapter):
    """
    Generic adapter for any OpenAI-compatible API.
    Auto-configures from known provider registry or manual config.
    """

    def __init__(self, provider_name: str = "", base_url: str = "",
                 api_key: str = "", env_key: str = ""):
        resolved_name = provider_name or "generic"
        self.provider_name = resolved_name
        self._strip_prefix = False

        # Auto-configure from known providers
        if resolved_name in KNOWN_PROVIDERS:
            info = KNOWN_PROVIDERS[resolved_name]
            self.base_url = base_url or info["base_url"]
            self._env_key = env_key or info["env_key"]
            self._strip_prefix = info.get("strip_prefix", False)
        else:
            self.base_url = base_url
            self._env_key = env_key

        resolved_key = api_key or os.environ.get(self._env_key, "")
        super().__init__(name=resolved_name, api_key=resolved_key)
        self._timeout = 30

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def format_model_id(self, model: str) -> str:
        """Normalize provider-specific model IDs, including OpenRouter-derived aliases."""
        normalized = super().format_model_id(model)
        if self._strip_prefix and "/" in normalized:
            return normalized.split("/", 1)[1]
        return normalized

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        if not self.api_key or not self.base_url:
            return result

        try:
            resp = requests.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code != 200:
                result.status = "auth_failed"
                return result

            data = resp.json().get("data", [])
            result.status = "authenticated"
            result.available_models = [
                f"{self.provider_name}/{item['id']}"
                for item in data
                if isinstance(item, dict) and item.get("id")
            ]
            return result
        except Exception as e:
            log.error(f"Generic discovery error ({self.provider_name}): {e}")
            result.status = "error"
            return result

    def execute(self, request: CompletionRequest) -> CompletionResponse:
        """Execute a chat completion request."""
        if not self.api_key:
            return CompletionResponse(
                model=request.model,
                content="",
                status="error",
                error=f"No API key for {self.provider_name}",
            )

        payload = {
            "model": request.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        start = time.time()
        try:
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.resolve_timeout(request, default=30.0, max_timeout=120.0),
            )
            latency = (time.time() - start) * 1000

            if r.status_code == 200:
                data = r.json()
                content = ""
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")

                usage = data.get("usage", {})
                self.circuit_breaker.record_success()
                return CompletionResponse(
                    model=data.get("model", request.model),
                    content=content,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    latency_ms=latency,
                    raw_response=data,
                    status="success",
                )

            self.circuit_breaker.record_failure()
            return CompletionResponse(
                model=request.model,
                content="",
                status="error",
                error=f"HTTP {r.status_code}: {r.text[:200]}",
                latency_ms=latency,
            )

        except requests.Timeout:
            self.circuit_breaker.record_failure()
            return CompletionResponse(
                model=request.model,
                content="",
                status="timeout",
                error="timeout",
            )
        except Exception as e:
            self.circuit_breaker.record_failure()
            return CompletionResponse(
                model=request.model,
                content="",
                status="error",
                error=str(e),
            )

    def health_check(self) -> bool:
        if not self.api_key or not self.base_url:
            return False
        try:
            resp = requests.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
