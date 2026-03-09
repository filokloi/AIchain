#!/usr/bin/env python3
"""
aichaind.routing.cost_optimizer — Cost-Aware Routing Optimizer

Selects the optimal provider/model combination based on:
  1. Balance available on each provider
  2. Token pricing from routing table
  3. Query complexity (heavy vs light)
  4. Subscription status (leverage paid plans when verifiably usable)
  5. Free tier availability
  6. Provider capability discovery

Goal: Maximum intelligence at minimum cost.
"""

import logging
from dataclasses import dataclass

log = logging.getLogger("aichaind.routing.cost_optimizer")

_DIRECT_PROVIDERS = {
    "openai", "google", "anthropic", "deepseek", "groq",
    "mistral", "xai", "cohere", "moonshot", "zhipu",
}
_PROVIDER_NAME_MAP = {
    "openai": "openai",
    "google": "google",
    "gemini": "google",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "groq": "groq",
    "openrouter": "openrouter",
    "mistral": "mistral",
    "xai": "xai",
    "cohere": "cohere",
    "moonshot": "moonshot",
    "zhipu": "zhipu",
}
_VERIFIED_DIRECT_MODELS = {
    "openai": {
        "heavy": "openai/gpt-4.1",
        "free": "openai/gpt-4.1-mini",
        "visual": "openai/gpt-4o",
    },
    "google": {
        "heavy": "google/gemini-2.5-pro",
        "free": "google/gemini-2.5-flash",
        "visual": "google/gemini-2.5-pro",
    },
    "anthropic": {
        "heavy": "anthropic/claude-sonnet-4",
        "free": "anthropic/claude-haiku-4.5",
        "visual": "anthropic/claude-sonnet-4",
    },
    "deepseek": {
        "heavy": "deepseek/deepseek-reasoner",
        "free": "deepseek/deepseek-chat",
        "visual": "deepseek/deepseek-chat",
    },
    "groq": {
        "heavy": "groq/llama-3.3-70b-versatile",
        "free": "groq/llama-3.1-8b-instant",
        "visual": "groq/llama-3.3-70b-versatile",
    },
}


@dataclass
class CostRoute:
    """A cost-optimized routing decision."""
    model: str
    provider: str
    estimated_cost_usd: float = 0.0
    reason: str = ""
    savings_vs_default: float = 0.0
    tier: str = ""


@dataclass
class ModelPricing:
    """Pricing info for a model."""
    model_id: str
    provider: str
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0
    is_free: bool = False
    quality_score: float = 0.5


class CostOptimizer:
    """
    Optimizes model selection based on cost, balance, quality, and real provider capabilities.
    """

    def __init__(self, pricing_table: dict = None):
        self._pricing: dict[str, ModelPricing] = {}
        self._budget_alerts: list[dict] = []
        self._provider_capabilities: dict[str, set[str]] = {}
        if pricing_table:
            self._load_pricing(pricing_table)

    def configure_provider_capabilities(self, capabilities: dict[str, set[str]]):
        """Attach discovered provider model availability."""
        normalized = {}
        for provider, models in (capabilities or {}).items():
            normalized[self._normalize_provider(provider)] = set(models or set())
        self._provider_capabilities = normalized

    def _load_pricing(self, table: dict):
        """Load pricing from routing table data."""
        self._pricing.clear()

        models = table.get("models")
        if isinstance(models, list) and models:
            for item in models:
                model_id = item.get("id", "")
                if not model_id:
                    continue
                pricing = item.get("pricing", {})
                self._pricing[model_id] = ModelPricing(
                    model_id=model_id,
                    provider=self._get_provider(model_id),
                    input_cost_per_1k=float(pricing.get("prompt", 0)) * 1000,
                    output_cost_per_1k=float(pricing.get("completion", 0)) * 1000,
                    is_free=":free" in model_id or float(pricing.get("prompt", 0)) == 0,
                    quality_score=_estimate_quality(model_id),
                )
            return

        hierarchy = table.get("routing_hierarchy", [])
        if not isinstance(hierarchy, list):
            return

        for item in hierarchy:
            model_id = item.get("model", "")
            if not model_id:
                continue
            metrics = item.get("metrics", {})
            total_cost_per_token = float(metrics.get("cost", 0) or 0)
            total_cost_per_1k = total_cost_per_token * 1000
            provider = self._provider_from_table(item)
            is_free = item.get("tier") in ("FREE_FRONTIER", "OAUTH_BRIDGE") or total_cost_per_token <= 0
            self._pricing[model_id] = ModelPricing(
                model_id=model_id,
                provider=provider,
                input_cost_per_1k=total_cost_per_1k,
                output_cost_per_1k=0.0,
                is_free=is_free,
                quality_score=_estimate_quality(model_id),
            )

    def optimize(
        self,
        model_preference: str,
        balance_report,
        available_models: dict = None,
        estimated_tokens: int = 1000,
        exclude_providers: set[str] | None = None,
        exclude_models: set[str] | None = None,
    ) -> CostRoute:
        """Select the cost-optimal model."""
        if not available_models:
            available_models = {}
        exclude_providers = {self._normalize_provider(p) for p in (exclude_providers or set())}
        exclude_models = set(exclude_models or set())

        default_model = available_models.get(model_preference, "")
        if default_model in exclude_models:
            default_model = ""

        for provider in ["google", "anthropic", "openai"]:
            if provider in exclude_providers:
                continue
            balance = balance_report.balances.get(provider)
            if balance and balance.is_subscription and self._is_verified_balance(balance):
                sub_model = self._find_subscription_model(provider, model_preference, available_models)
                if sub_model and sub_model not in exclude_models and self._model_supported(provider, sub_model):
                    return CostRoute(
                        model=sub_model,
                        provider=provider,
                        estimated_cost_usd=0.0,
                        reason=f"subscription_leverage:{provider}",
                        tier="subscription",
                    )

        if model_preference == "free":
            free_model = available_models.get("free", "")
            if (
                free_model
                and free_model not in exclude_models
                and self._get_provider(free_model) not in exclude_providers
                and self._model_is_credible(free_model, balance_report)
            ):
                return CostRoute(
                    model=free_model,
                    provider=self._get_provider(free_model),
                    estimated_cost_usd=0.0,
                    reason="free_tier_sufficient",
                    tier="free",
                )

        if model_preference == "visual":
            visual_model = available_models.get("visual", default_model)
            if (
                visual_model
                and visual_model not in exclude_models
                and self._get_provider(visual_model) not in exclude_providers
                and self._model_is_credible(visual_model, balance_report)
            ):
                return CostRoute(
                    model=visual_model,
                    provider=self._get_provider(visual_model),
                    estimated_cost_usd=self._estimate_cost(visual_model, estimated_tokens),
                    reason="visual_route_preserved",
                    tier="pay-per-token",
                )

        providers_with_money = [
            self._normalize_provider(provider)
            for provider in balance_report.providers_with_credits
            if self._normalize_provider(provider) not in exclude_providers
            and (
                balance_report.balances.get(self._normalize_provider(provider), None) is None
                or balance_report.balances[self._normalize_provider(provider)].has_credits
            )
        ]
        if not providers_with_money:
            free_model = available_models.get("free", default_model)
            self._add_alert("no_credits", "All providers have zero balance")
            return CostRoute(
                model=free_model,
                provider=self._get_provider(free_model),
                estimated_cost_usd=0.0,
                reason="no_credits_fallback_free",
                tier="free",
            )

        candidates = []
        for model_id, pricing in self._pricing.items():
            if model_id in exclude_models:
                continue
            provider = pricing.provider
            if provider not in providers_with_money:
                continue
            if not self._model_supported(provider, model_id):
                continue
            if model_preference == "heavy" and pricing.quality_score < 0.6:
                continue

            est_cost = (
                pricing.input_cost_per_1k * estimated_tokens / 1000
                + pricing.output_cost_per_1k * estimated_tokens / 1000
            )
            candidates.append((
                self._provider_rank(provider, balance_report),
                est_cost,
                -pricing.quality_score,
                model_id,
                pricing,
            ))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1], item[2]))
            _, best_cost, _, best_model, best_pricing = candidates[0]
            default_cost = self._estimate_cost(default_model, estimated_tokens)
            return CostRoute(
                model=best_model,
                provider=best_pricing.provider,
                estimated_cost_usd=best_cost,
                reason=f"cost_optimized:{best_pricing.provider}",
                savings_vs_default=max(0, default_cost - best_cost),
                tier="free" if best_pricing.is_free else "pay-per-token",
            )

        verified_fallbacks = []
        for provider in providers_with_money:
            if provider == "openrouter":
                continue
            fallback_model = self._find_verified_direct_fallback_model(provider, model_preference, available_models)
            if not fallback_model or fallback_model in exclude_models:
                continue
            balance = balance_report.balances.get(provider)
            tier = "subscription" if balance and balance.is_subscription else (
                "free" if balance and balance.is_free_tier else "pay-per-token"
            )
            verified_fallbacks.append((
                self._provider_rank(provider, balance_report),
                self._estimate_cost(fallback_model, estimated_tokens),
                provider,
                fallback_model,
                tier,
            ))

        if verified_fallbacks:
            verified_fallbacks.sort(key=lambda item: (item[0], item[1], item[2]))
            _, est_cost, provider, fallback_model, tier = verified_fallbacks[0]
            return CostRoute(
                model=fallback_model,
                provider=provider,
                estimated_cost_usd=est_cost,
                reason=f"verified_direct_fallback:{provider}",
                tier=tier,
            )

        total = balance_report.total_available_usd
        if 0 < total < 1.0:
            self._add_alert("low_balance", f"Total balance: ${total:.2f}")

        return CostRoute(
            model=default_model,
            provider=self._get_provider(default_model),
            estimated_cost_usd=self._estimate_cost(default_model, estimated_tokens),
            reason="default_routing",
            tier="pay-per-token",
        )

    def _find_subscription_model(self, provider: str, preference: str, available: dict) -> str:
        preferred = _VERIFIED_DIRECT_MODELS.get(provider, {}).get(preference, "")
        return preferred or available.get(preference, "")

    def _find_verified_direct_fallback_model(self, provider: str, preference: str, available: dict) -> str:
        preferred = _VERIFIED_DIRECT_MODELS.get(provider, {}).get(preference, "")
        if preferred and self._model_supported(provider, preferred):
            return preferred
        candidate = available.get(preference, "")
        if candidate and self._get_provider(candidate) == provider and self._model_supported(provider, candidate):
            return candidate
        return ""

    def _normalize_provider(self, provider: str) -> str:
        return _PROVIDER_NAME_MAP.get((provider or "").strip().lower(), (provider or "").strip().lower())

    def _provider_from_table(self, item: dict) -> str:
        provider_name = self._normalize_provider(item.get("provider", ""))
        model_id = item.get("model", "")
        model_provider = self._get_provider(model_id)
        if provider_name in _DIRECT_PROVIDERS or provider_name == "openrouter":
            return provider_name
        return model_provider

    def _normalize_model_aliases(self, provider: str, model_id: str) -> set[str]:
        model_id = (model_id or "").strip()
        aliases = {model_id}
        if not model_id:
            return aliases
        normalized_provider = self._normalize_provider(provider)
        if "/" in model_id:
            prefix, rest = model_id.split("/", 1)
            aliases.add(rest)
            if prefix != normalized_provider:
                aliases.add(f"{normalized_provider}/{rest}")
        else:
            aliases.add(f"{normalized_provider}/{model_id}")
        return aliases

    def _model_supported(self, provider: str, model_id: str) -> bool:
        capabilities = self._provider_capabilities.get(self._normalize_provider(provider))
        if not capabilities:
            return True
        aliases = self._normalize_model_aliases(provider, model_id)
        return any(alias in capabilities for alias in aliases)

    def _is_verified_balance(self, balance) -> bool:
        if not balance or not balance.has_credits:
            return False
        if balance.balance_usd > 0:
            return True
        if balance.is_free_tier:
            return True
        return balance.source == "api"

    def _model_is_credible(self, model_id: str, balance_report) -> bool:
        provider = self._get_provider(model_id)
        if not self._model_supported(provider, model_id):
            return False
        balance = balance_report.balances.get(provider)
        if balance is None:
            return provider != "openrouter"
        if provider == "openrouter":
            return balance.has_credits and (balance.balance_usd > 0 or balance.source == "api")
        return self._is_verified_balance(balance)

    def _provider_rank(self, provider: str, balance_report) -> int:
        balance = balance_report.balances.get(provider)
        if not balance or not balance.has_credits:
            return 999

        rank = 100
        if provider == "openrouter":
            rank += 40
        else:
            rank -= 10

        if balance.is_free_tier:
            rank -= 30
        if balance.source == "api" and balance.balance_usd > 0:
            rank -= 20
        elif balance.source == "api":
            rank -= 15
        if balance.is_subscription and not self._is_verified_balance(balance):
            rank += 25
        if balance.error:
            rank += 30
        return rank

    def _get_provider(self, model_id: str) -> str:
        if not model_id:
            return "unknown"
        prefix = model_id.split("/", 1)[0].lower() if "/" in model_id else model_id.lower()
        if prefix in _DIRECT_PROVIDERS or prefix == "openrouter":
            return prefix
        return "openrouter"

    def _estimate_cost(self, model_id: str, tokens: int) -> float:
        pricing = self._pricing.get(model_id)
        if not pricing:
            return 0.0
        return (pricing.input_cost_per_1k + pricing.output_cost_per_1k) * tokens / 1000

    def _add_alert(self, alert_type: str, message: str):
        self._budget_alerts.append({"type": alert_type, "message": message})
        log.warning(f"Budget alert [{alert_type}]: {message}")

    @property
    def alerts(self) -> list[dict]:
        return self._budget_alerts

    def clear_alerts(self):
        self._budget_alerts.clear()


def _estimate_quality(model_id: str) -> float:
    mid = model_id.lower()
    if any(x in mid for x in ("o3-pro", "gpt-5", "opus", "gemini-3")):
        return 0.95
    if any(x in mid for x in ("gpt-4", "sonnet-4", "gemini-2.5-pro", "o4")):
        return 0.88
    if any(x in mid for x in ("deepseek-r1", "deepseek-reasoner", "claude-3.7", "gemini-2.5-flash")):
        return 0.82
    if any(x in mid for x in ("gpt-4o", "deepseek-chat", "llama-3.3")):
        return 0.75
    if any(x in mid for x in ("mini", "flash", "haiku", "nano")):
        return 0.65
    if ":free" in mid:
        return 0.55
    return 0.70
