#!/usr/bin/env python3
"""
aichaind.providers.discovery — Provider Auto-Discovery

Reads API keys from OpenClaw config and environment.
Detects which providers the user has direct access to.
Builds a priority-ordered provider preference list.

Rules:
  1. Direct API key with subscription (OpenAI Plus, Gemini Pro) → highest priority
  2. Direct API key (pay-per-token) → high priority
  3. OpenRouter (single key, many models) → fallback
  4. Free tiers → lowest priority
"""

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

log = logging.getLogger("aichaind.providers.discovery")

# Standard OpenClaw config path
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"


@dataclass
class ProviderCredential:
    """Detected provider credential."""
    provider: str           # e.g., "openai", "deepseek", "google"
    api_key: str = ""
    source: str = ""        # "openclaw_config", "env_var", "manual"
    has_subscription: bool = False  # True if user has paid subscription
    priority: int = 50      # Lower = higher priority


@dataclass
class DiscoveryReport:
    """Result of provider auto-discovery."""
    credentials: list[ProviderCredential] = field(default_factory=list)
    total_providers: int = 0
    direct_providers: list[str] = field(default_factory=list)
    fallback_via_openrouter: bool = False


# ─────────────────────────────────────────
# KEY DETECTION
# ─────────────────────────────────────────

# Map of env var names → provider names
KEY_MAP = {
    "OPENAI_API_KEY": "openai",
    "DEEPSEEK_API_KEY": "deepseek",
    "GOOGLE_API_KEY": "google",
    "GEMINI_API_KEY": "google",
    "GEMINI_KEY": "google",
    "GROQ_API_KEY": "groq",
    "ANTHROPIC_API_KEY": "anthropic",
    "MISTRAL_API_KEY": "mistral",
    "XAI_API_KEY": "xai",
    "COHERE_API_KEY": "cohere",
    "MOONSHOT_API_KEY": "moonshot",
    "ZHIPU_API_KEY": "zhipu",
    "OPENROUTER_API_KEY": "openrouter",
    "OPENROUTER_KEY": "openrouter",
    "DEEPSEEK_KEY": "deepseek",
}

# Providers with known subscription tiers
SUBSCRIPTION_PROVIDERS = {
    "openai": {"key_prefix": "sk-proj-", "sub_name": "OpenAI Plus/Pro"},
    "google": {"key_prefix": "AIza", "sub_name": "Gemini Pro"},
    "anthropic": {"key_prefix": "sk-ant-", "sub_name": "Anthropic API"},
}

# Provider priority (lower = preferred)
# Direct API with subscription = 10, direct API = 20, OpenRouter = 30, free = 40
PRIORITY_SUBSCRIPTION = 10
PRIORITY_DIRECT = 20
PRIORITY_OPENROUTER = 30
PRIORITY_FREE = 40


def discover_providers(openclaw_config_path: Path = None) -> DiscoveryReport:
    """
    Auto-discover all available provider API keys.

    Checks:
    1. OpenClaw config file (env.vars section)
    2. System environment variables
    3. Detects subscription status from key format

    Returns a DiscoveryReport with prioritized credentials.
    """
    report = DiscoveryReport()
    seen_providers = {}  # provider → ProviderCredential

    config_path = openclaw_config_path or OPENCLAW_CONFIG

    # ── 1. Read OpenClaw config ──
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            env_vars = cfg.get("env", {}).get("vars", {})

            for var_name, provider in KEY_MAP.items():
                key = env_vars.get(var_name, "")
                if key and not key.startswith("${") and len(key) > 8:
                    if provider not in seen_providers:
                        cred = _build_credential(provider, key, "openclaw_config")
                        seen_providers[provider] = cred

            # Also inject keys into os.environ so adapters can find them
            for var_name, key in env_vars.items():
                if key and not key.startswith("${") and var_name in KEY_MAP:
                    os.environ.setdefault(var_name, key)

            log.info(f"Loaded {len(seen_providers)} providers from OpenClaw config")

        except Exception as e:
            log.warning(f"Could not read OpenClaw config: {e}")

    # ── 2. Check environment variables ──
    for var_name, provider in KEY_MAP.items():
        key = os.environ.get(var_name, "")
        if key and len(key) > 8 and provider not in seen_providers:
            cred = _build_credential(provider, key, "env_var")
            seen_providers[provider] = cred

    # ── 3. Build report ──
    for provider, cred in seen_providers.items():
        report.credentials.append(cred)
        if provider != "openrouter":
            report.direct_providers.append(provider)

    report.credentials.sort(key=lambda c: c.priority)
    report.total_providers = len(report.credentials)
    report.fallback_via_openrouter = "openrouter" in seen_providers

    return report


def _build_credential(provider: str, key: str, source: str) -> ProviderCredential:
    """Build a credential with subscription detection."""
    has_sub = False
    priority = PRIORITY_DIRECT

    # Check subscription status
    if provider in SUBSCRIPTION_PROVIDERS:
        sub_info = SUBSCRIPTION_PROVIDERS[provider]
        if key.startswith(sub_info["key_prefix"]):
            has_sub = True
            priority = PRIORITY_SUBSCRIPTION
            log.info(f"  {provider}: {sub_info['sub_name']} subscription detected")

    if provider == "openrouter":
        priority = PRIORITY_OPENROUTER

    return ProviderCredential(
        provider=provider,
        api_key=key,
        source=source,
        has_subscription=has_sub,
        priority=priority,
    )


def get_preferred_adapter_order(report: DiscoveryReport) -> list[str]:
    """
    Get the preferred adapter order based on discovered credentials.
    Direct APIs with subscriptions first, then direct API, then OpenRouter.
    """
    return [c.provider for c in report.credentials]


def inject_keys_into_env(report: DiscoveryReport):
    """Inject discovered keys into os.environ for adapter use."""
    var_reverse = {}
    for var_name, provider in KEY_MAP.items():
        if provider not in var_reverse:
            var_reverse[provider] = var_name

    for cred in report.credentials:
        var_name = var_reverse.get(cred.provider, "")
        if var_name and cred.api_key:
            os.environ[var_name] = cred.api_key
