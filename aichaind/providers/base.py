#!/usr/bin/env python3
"""
aichaind.providers.base — Provider Adapter Abstract Base Class

Every provider adapter (OpenRouter, Gemini, Groq, OpenAI, DeepSeek, A2A)
must implement this contract.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import time
import logging

log = logging.getLogger("aichaind.providers")


# ─────────────────────────────────────────
# REQUEST / RESPONSE CONTRACTS
# ─────────────────────────────────────────

@dataclass
class CompletionRequest:
    """Normalized request to any provider."""
    model: str
    messages: list[dict]
    max_tokens: int = 4096
    temperature: float = 0.7
    stream: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class CompletionResponse:
    """Normalized response from any provider."""
    model: str
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str = ""
    raw_response: dict = field(default_factory=dict)
    error: str = ""
    status: str = "success"  # success, error, timeout


@dataclass
class DiscoveryResult:
    """Result of provider capability discovery."""
    status: str = "unconfigured"  # unconfigured, authenticated, auth_failed, error
    available_models: list[str] = field(default_factory=list)
    cost_mode: str = "api-per-token"
    limits: dict = field(default_factory=dict)


# ─────────────────────────────────────────
# CIRCUIT BREAKER (per-provider)
# ─────────────────────────────────────────

class ProviderCircuitBreaker:
    """Per-provider circuit breaker to prevent cascading failures."""

    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._failures: int = 0
        self._state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self._opened_at: float = 0.0

    @property
    def is_available(self) -> bool:
        if self._state == "CLOSED":
            return True
        if self._state == "OPEN":
            if time.time() - self._opened_at > self.reset_timeout:
                self._state = "HALF_OPEN"
                return True
            return False
        # HALF_OPEN: allow one test request
        return True

    def record_success(self):
        self._failures = 0
        self._state = "CLOSED"

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = "OPEN"
            self._opened_at = time.time()
            log.warning(f"Circuit breaker OPEN (failures: {self._failures})")

    @property
    def state(self) -> str:
        # Re-evaluate state on read
        if self._state == "OPEN" and time.time() - self._opened_at > self.reset_timeout:
            self._state = "HALF_OPEN"
        return self._state


# ─────────────────────────────────────────
# ADAPTER ABC
# ─────────────────────────────────────────

class ProviderAdapter(ABC):
    """
    Abstract base class for all provider adapters.

    Each adapter handles:
    - Discovery: what models are available
    - Execution: send a request, get a response
    - Health: is the provider reachable
    - Circuit breaker: per-provider failure tracking
    """

    def __init__(self, name: str, api_key: str = ""):
        self.name = name
        self._api_key = api_key or ""
        self.circuit_breaker = ProviderCircuitBreaker()

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str):
        self._api_key = value or ""

    @abstractmethod
    def discover(self) -> DiscoveryResult:
        """Discover available models and capabilities."""
        ...

    @abstractmethod
    def execute(self, request: CompletionRequest) -> CompletionResponse:
        """Execute a completion request. Must handle errors gracefully."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Quick connectivity check. Return True if reachable."""
        ...

    def supports_streaming(self) -> bool:
        """Override to True if the adapter supports streaming."""
        return False

    def format_model_id(self, model_id: str) -> str:
        """Normalize OpenRouter-derived model IDs for native provider APIs."""
        normalized = (model_id or "").strip()
        if not normalized:
            return normalized

        if self.name.lower() != "openrouter" and normalized.lower().startswith("openrouter/"):
            normalized = normalized.split("/", 1)[1]

        if "/" in normalized:
            prefix, rest = normalized.split("/", 1)
            if prefix.lower() == self.name.lower():
                normalized = rest

        if self.name.lower() != "openrouter" and normalized.lower().endswith(":free"):
            normalized = normalized[:-5]

        return normalized
