#!/usr/bin/env python3
"""
aichaind.providers.adapters.a2a — Agent-to-Agent Adapter Stub (v1)

Future: delegates tasks to external specialized agents via A2A protocol.
v1: stub only — raises NotImplementedError to trigger fallback chain.
"""

import logging
from aichaind.providers.base import (
    ProviderAdapter, CompletionRequest, CompletionResponse, DiscoveryResult
)

log = logging.getLogger("aichaind.providers.a2a")


class A2AAdapter(ProviderAdapter):
    """
    Agent-to-Agent adapter stub.

    Design intent: AIchain will later delegate tasks to specialized
    external agents rather than raw models. This stub ensures the
    adapter interface is in place for v2.0 implementation.

    v1: Always returns error → triggers fallback to classic LLM adapter.
    """

    def __init__(self, api_key: str = ""):
        super().__init__(name="a2a", api_key=api_key)

    def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            status="unconfigured",
            available_models=[],
            cost_mode="per-task",
        )

    def execute(self, request: CompletionRequest) -> CompletionResponse:
        log.info("A2A adapter invoked — not yet implemented, triggering fallback")
        return CompletionResponse(
            model=request.model,
            content="",
            error="A2A protocol not yet available — routed to fallback",
            status="error",
        )

    def health_check(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return False
