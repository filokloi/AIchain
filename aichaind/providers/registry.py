#!/usr/bin/env python3
"""
aichaind.providers.registry — Provider Adapter Registry

Factory pattern for creating and managing provider adapters.
Maps provider prefixes to adapter classes.
"""

import logging
from typing import Optional

from aichaind.providers.base import ProviderAdapter

log = logging.getLogger("aichaind.providers.registry")


# ─────────────────────────────────────────
# REGISTRY
# ─────────────────────────────────────────

_ADAPTER_CLASSES: dict[str, type] = {}


def register_adapter(prefix: str, adapter_class: type):
    """Register an adapter class for a provider prefix."""
    _ADAPTER_CLASSES[prefix] = adapter_class


def _load_defaults():
    """Lazy-load default adapter classes."""
    if _ADAPTER_CLASSES:
        return

    from aichaind.providers.adapters.openrouter import OpenRouterAdapter
    from aichaind.providers.adapters.gemini import GeminiAdapter
    from aichaind.providers.adapters.groq import GroqAdapter
    from aichaind.providers.adapters.deepseek import DeepSeekAdapter
    from aichaind.providers.adapters.openai_native import OpenAINativeAdapter
    from aichaind.providers.adapters.generic import GenericOpenAIAdapter
    from aichaind.providers.adapters.local_openai import LocalOpenAIAdapter

    register_adapter("openrouter", OpenRouterAdapter)
    register_adapter("google", GeminiAdapter)
    register_adapter("groq", GroqAdapter)
    register_adapter("deepseek", DeepSeekAdapter)
    register_adapter("openai", OpenAINativeAdapter)

    # Local OpenAI-compatible runtimes
    for provider_name in ("local", "vllm", "ollama", "lmstudio", "llamacpp"):
        register_adapter(provider_name, lambda pn=provider_name: LocalOpenAIAdapter(pn))

    # Generic OpenAI-compatible cloud providers
    for provider_name in ("mistral", "xai", "cohere", "moonshot", "zhipu"):
        register_adapter(provider_name, lambda pn=provider_name: GenericOpenAIAdapter(pn))


def get_adapter(provider_prefix: str) -> Optional[ProviderAdapter]:
    """Get an adapter instance for a given provider prefix."""
    _load_defaults()
    prefix = (provider_prefix or "").lower()
    cls_or_factory = _ADAPTER_CLASSES.get(prefix)
    if cls_or_factory is None:
        from aichaind.providers.adapters.generic import GenericOpenAIAdapter, KNOWN_PROVIDERS

        if prefix in KNOWN_PROVIDERS:
            return GenericOpenAIAdapter(prefix)
        cls_or_factory = _ADAPTER_CLASSES.get("openrouter")
    if cls_or_factory is None:
        return None
    if callable(cls_or_factory) and not isinstance(cls_or_factory, type):
        return cls_or_factory()
    return cls_or_factory()


def get_adapter_for_model(model_id: str) -> Optional[ProviderAdapter]:
    """Get the appropriate adapter for a model ID."""
    if "/" in model_id:
        prefix = model_id.split("/", 1)[0].lower()
        _load_defaults()
        if prefix in _ADAPTER_CLASSES:
            return get_adapter(prefix)
    return get_adapter("openrouter")


def list_providers() -> list[str]:
    """List all registered provider prefixes."""
    _load_defaults()
    return list(_ADAPTER_CLASSES.keys())


def discover_all() -> dict:
    """Run discovery on all registered providers."""
    _load_defaults()
    results = {}
    for prefix, cls in _ADAPTER_CLASSES.items():
        try:
            adapter = cls() if isinstance(cls, type) else cls()
            results[prefix] = adapter.discover()
        except Exception as e:
            log.error(f"Discovery failed for {prefix}: {e}")
    return results
