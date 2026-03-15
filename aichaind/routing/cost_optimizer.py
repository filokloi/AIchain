#!/usr/bin/env python3
"""
aichaind.routing.cost_optimizer — Local Effective Score Optimizer

Selects the optimal provider/model combination based on:
  1. Global catalog quality signals (intelligence, speed, stability, rank)
  2. Real provider access mode (local, API key, OAuth, connectors)
  3. Effective marginal cost for the current user
  4. Balance / quota confidence
  5. Preference fit for the current task (free / heavy / visual / local)

Goal: catalog first, access second, user-effective routing third.
"""

import logging
from dataclasses import dataclass

log = logging.getLogger("aichaind.routing.cost_optimizer")

_LOCAL_PROVIDERS = {"local", "vllm", "ollama", "lmstudio", "llamacpp"}
_DIRECT_PROVIDERS = {
    "openai", "openai-codex", "google", "anthropic", "deepseek", "groq",
    "mistral", "xai", "cohere", "moonshot", "zhipu", *tuple(_LOCAL_PROVIDERS),
}
_PROVIDER_NAME_MAP = {
    "openai": "openai",
    "openai-codex": "openai-codex",
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
    "local": "local",
    "vllm": "vllm",
    "ollama": "ollama",
    "lmstudio": "lmstudio",
    "llamacpp": "llamacpp",
}
_ZERO_MARGINAL_METHODS = {"local", "oauth", "workspace_connector", "enterprise_connector"}
_VERIFIED_DIRECT_MODELS = {
    "openai": {
        "heavy": "openai/gpt-4.1",
        "free": "openai/gpt-4.1-mini",
        "visual": "openai/gpt-4o",
    },
    "openai-codex": {
        "heavy": "openai-codex/gpt-5.4",
        "free": "openai-codex/gpt-5.4",
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
    local_effective_score: float = 0.0
    access_method: str = ""


@dataclass
class ModelPricing:
    """Pricing and quality info for a model."""
    model_id: str
    provider: str
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0
    is_free: bool = False
    quality_score: float = 0.5
    speed_score: float = 0.7
    stability_score: float = 0.75
    global_rank: int = 9999


@dataclass
class _AccessSnapshot:
    provider: str
    selected_method: str = "api_key"
    status: str = "runtime_confirmed"
    runtime_confirmed: bool = True
    target_form_reached: bool = True
    quota_visibility: str = "provider_console"
    billing_basis: str = ""
    preferred_model: str = ""
    verified_models: list[str] = None
    target_model: str = ""


class CostOptimizer:
    """Optimizes model selection using local effective score semantics."""

    def __init__(self, pricing_table: dict = None):
        self._pricing: dict[str, ModelPricing] = {}
        self._budget_alerts: list[dict] = []
        self._provider_capabilities: dict[str, set[str]] = {}
        self._provider_access_layer = None
        self._local_profiles: dict[str, dict] = {}
        self._routing_preferences: dict[str, object] = {
            "prefer_prepaid_premium": False,
            "prepaid_premium_providers": set(),
        }
        if pricing_table:
            self._load_pricing(pricing_table)

    def configure_provider_capabilities(self, capabilities: dict[str, set[str]]):
        normalized = {}
        for provider, models in (capabilities or {}).items():
            normalized[self._normalize_provider(provider)] = set(models or set())
        self._provider_capabilities = normalized

    def configure_provider_access_layer(self, layer) -> None:
        self._provider_access_layer = layer

    def configure_local_profiles(self, snapshot: dict | None) -> None:
        if isinstance(snapshot, dict) and isinstance(snapshot.get('profiles'), dict):
            self._local_profiles = dict(snapshot.get('profiles', {}))
        elif isinstance(snapshot, dict):
            self._local_profiles = dict(snapshot)
        else:
            self._local_profiles = {}

    def configure_routing_preferences(self, routing_cfg: dict | None) -> None:
        cfg = routing_cfg if isinstance(routing_cfg, dict) else {}
        providers = {
            self._normalize_provider(provider)
            for provider in (cfg.get("prepaid_premium_providers") or [])
            if str(provider or "").strip()
        }
        self._routing_preferences = {
            "prefer_prepaid_premium": bool(cfg.get("prefer_prepaid_premium", False)),
            "prepaid_premium_providers": providers,
        }

    def _load_pricing(self, table: dict):
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
                    speed_score=_estimate_speed(model_id),
                    stability_score=_estimate_stability(model_id),
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
            intelligence = _normalize_metric(metrics.get("intelligence"), fallback=_estimate_quality(model_id))
            speed = _normalize_metric(metrics.get("speed"), fallback=_estimate_speed(model_id))
            stability = _normalize_metric(metrics.get("stability"), fallback=_estimate_stability(model_id))
            self._pricing[model_id] = ModelPricing(
                model_id=model_id,
                provider=provider,
                input_cost_per_1k=total_cost_per_1k,
                output_cost_per_1k=0.0,
                is_free=is_free,
                quality_score=intelligence,
                speed_score=speed,
                stability_score=stability,
                global_rank=int(item.get("rank") or 9999),
            )

    def optimize(
        self,
        model_preference: str,
        balance_report,
        available_models: dict = None,
        estimated_tokens: int = 1000,
        exclude_providers: set[str] | None = None,
        exclude_models: set[str] | None = None,
        current_model: str = "",
        current_provider: str = "",
        task_hint: str = "",
        routing_preference: str = "balanced",
    ) -> CostRoute:
        if not available_models:
            available_models = {}
        routing_preference = self._normalize_user_routing_preference(routing_preference)
        exclude_providers = {self._normalize_provider(p) for p in (exclude_providers or set())}
        exclude_models = set(exclude_models or set())
        providers_with_money = [
            self._normalize_provider(provider)
            for provider in balance_report.providers_with_credits
            if self._normalize_provider(provider) not in exclude_providers
            and (
                balance_report.balances.get(self._normalize_provider(provider), None) is None
                or balance_report.balances[self._normalize_provider(provider)].has_credits
            )
        ]

        default_model = current_model or available_models.get(model_preference, "") or available_models.get("free", "")
        if default_model in exclude_models:
            default_model = ""

        if not providers_with_money and not self._has_zero_marginal_runtime_path(available_models, exclude_providers, exclude_models):
            free_model = available_models.get("free", default_model)
            self._add_alert("no_credits", "All providers have zero balance")
            return self._build_route(
                model_id=free_model,
                model_preference=model_preference,
                available_models=available_models,
                balance_report=balance_report,
                estimated_tokens=estimated_tokens,
                reason="no_credits_fallback_free",
                forced_tier="free",
                task_hint=task_hint,
            )

        if model_preference == "visual":
            visual_model = available_models.get("visual", default_model)
            if (
                visual_model
                and visual_model not in exclude_models
                and self._get_provider(visual_model) not in exclude_providers
                and self._model_is_credible(visual_model, balance_report)
            ):
                return self._build_route(
                    model_id=visual_model,
                    model_preference=model_preference,
                    available_models=available_models,
                    balance_report=balance_report,
                    estimated_tokens=estimated_tokens,
                    reason="visual_route_preserved",
                    task_hint=task_hint,
                )

        if routing_preference == "prefer_local":
            local_route = self._select_user_preferred_local_route(
                model_preference=model_preference,
                available_models=available_models,
                estimated_tokens=estimated_tokens,
                balance_report=balance_report,
                exclude_providers=exclude_providers,
                exclude_models=exclude_models,
                task_hint=task_hint,
            )
            if local_route:
                return local_route

        prepaid_route = self._select_prepaid_premium_route(
            model_preference=model_preference,
            available_models=available_models,
            estimated_tokens=estimated_tokens,
            balance_report=balance_report,
            exclude_providers=exclude_providers,
            exclude_models=exclude_models,
            task_hint=task_hint,
        )
        if prepaid_route:
            return prepaid_route

        runtime_route = self._select_runtime_zero_marginal_route(
            model_preference=model_preference,
            available_models=available_models,
            estimated_tokens=estimated_tokens,
            balance_report=balance_report,
            exclude_providers=exclude_providers,
            exclude_models=exclude_models,
            task_hint=task_hint,
        )
        if runtime_route:
            return runtime_route

        for provider in ["google", "anthropic", "openai", "openai-codex"]:
            if provider in exclude_providers:
                continue
            balance = balance_report.balances.get(provider)
            if balance and balance.is_subscription and self._is_verified_balance(balance):
                sub_model = self._find_subscription_model(provider, model_preference, available_models, self._resolve_access(provider))
                if sub_model and sub_model not in exclude_models and self._model_supported(provider, sub_model) and self._provider_task_allowed(provider, model_preference, task_hint):
                    return self._build_route(
                        model_id=sub_model,
                        model_preference=model_preference,
                        available_models=available_models,
                        balance_report=balance_report,
                        estimated_tokens=estimated_tokens,
                        reason=f"subscription_leverage:{provider}",
                        task_hint=task_hint,
                    )

        if model_preference == "free" and routing_preference != "max_intelligence":
            free_model = available_models.get("free", "")
            if (
                free_model
                and free_model not in exclude_models
                and self._get_provider(free_model) not in exclude_providers
                and self._model_is_credible(free_model, balance_report)
            ):
                return self._build_route(
                    model_id=free_model,
                    model_preference=model_preference,
                    available_models=available_models,
                    balance_report=balance_report,
                    estimated_tokens=estimated_tokens,
                    reason="free_tier_sufficient",
                    task_hint=task_hint,
                )

        candidates = []
        candidate_model_ids = set(self._pricing.keys())
        candidate_model_ids.update(model_id for model_id in available_models.values() if model_id)
        for model_id in candidate_model_ids:
            if model_id in exclude_models:
                continue
            provider = self._get_provider(model_id)
            pricing = self._pricing_for_candidate(model_id, provider)
            if provider in exclude_providers:
                continue
            access = self._resolve_access(provider)
            if provider in _LOCAL_PROVIDERS:
                if not self._local_candidate_is_viable_for_task(model_id, model_preference, task_hint, estimated_tokens):
                    continue
            elif provider not in providers_with_money:
                continue
            if not self._access_candidate_allowed(provider, access):
                continue
            if not self._provider_task_allowed(provider, model_preference, task_hint):
                continue
            if not self._candidate_has_budget(provider, balance_report, access, pricing):
                continue
            if not self._model_supported(provider, model_id):
                continue
            if model_preference == "heavy" and pricing.quality_score < 0.6:
                continue
            if not self._preference_allows_candidate(model_preference, model_id, pricing, available_models):
                continue

            est_cost = self._estimate_effective_cost(model_id, provider, estimated_tokens, access, balance_report, pricing)
            local_score = self._local_effective_score(
                pricing=pricing,
                model_preference=model_preference,
                model_id=model_id,
                provider=provider,
                access=access,
                estimated_cost_usd=est_cost,
                available_models=available_models,
                task_hint=task_hint,
            )
            candidates.append((
                self._provider_rank(provider, balance_report, access),
                est_cost,
                -pricing.quality_score,
                -local_score,
                model_id,
            ))

        if candidates:
            candidates.sort()
            _, best_cost, _, _, best_model = candidates[0]
            default_cost = self._estimate_cost(default_model, estimated_tokens)
            route = self._build_route(
                model_id=best_model,
                model_preference=model_preference,
                available_models=available_models,
                balance_report=balance_report,
                estimated_tokens=estimated_tokens,
                reason=f"cost_optimized:{self._get_provider(best_model)}",
                task_hint=task_hint,
            )
            route.savings_vs_default = max(0.0, default_cost - best_cost)
            return route

        verified_fallbacks = []
        for provider in providers_with_money:
            if provider == "openrouter":
                continue
            fallback_model = self._find_verified_direct_fallback_model(provider, model_preference, available_models, access)
            if not fallback_model or fallback_model in exclude_models:
                continue
            access = self._resolve_access(provider)
            pricing = self._pricing_for_candidate(fallback_model, provider)
            if not self._access_candidate_allowed(provider, access):
                continue
            if not self._provider_task_allowed(provider, model_preference, task_hint):
                continue
            if not self._candidate_has_budget(provider, balance_report, access, pricing):
                continue
            est_cost = self._estimate_effective_cost(fallback_model, provider, estimated_tokens, access, balance_report, pricing)
            local_score = self._local_effective_score(
                pricing=pricing,
                model_preference=model_preference,
                model_id=fallback_model,
                provider=provider,
                access=access,
                estimated_cost_usd=est_cost,
                available_models=available_models,
                task_hint=task_hint,
            )
            verified_fallbacks.append((
                self._provider_rank(provider, balance_report, access),
                -local_score,
                self._estimate_cost(fallback_model, estimated_tokens),
                provider,
                fallback_model,
            ))

        if verified_fallbacks:
            verified_fallbacks.sort()
            _, _, _, provider, fallback_model = verified_fallbacks[0]
            return self._build_route(
                model_id=fallback_model,
                model_preference=model_preference,
                available_models=available_models,
                balance_report=balance_report,
                estimated_tokens=estimated_tokens,
                reason=f"verified_direct_fallback:{provider}",
                task_hint=task_hint,
            )

        total = balance_report.total_available_usd
        if 0 < total < 1.0:
            self._add_alert("low_balance", f"Total balance: ${total:.2f}")

        return self._build_route(
            model_id=default_model,
            model_preference=model_preference,
            available_models=available_models,
            balance_report=balance_report,
            estimated_tokens=estimated_tokens,
            reason="default_routing",
            task_hint=task_hint,
        )
    def _find_subscription_model(self, provider: str, preference: str, available: dict, access: _AccessSnapshot | None = None) -> str:
        access = access or self._resolve_access(provider)
        preferred = str(access.preferred_model or "").strip()
        if preferred and self._model_supported(provider, preferred):
            return preferred
        preferred = _VERIFIED_DIRECT_MODELS.get(provider, {}).get(preference, "")
        return preferred or available.get(preference, "")

    def _find_verified_direct_fallback_model(self, provider: str, preference: str, available: dict, access: _AccessSnapshot | None = None) -> str:
        access = access or self._resolve_access(provider)
        preferred = str(access.preferred_model or "").strip()
        if preferred and self._model_supported(provider, preferred):
            return preferred
        preferred = _VERIFIED_DIRECT_MODELS.get(provider, {}).get(preference, "")
        if preferred and self._model_supported(provider, preferred):
            return preferred
        candidate = available.get(preference, "")
        if candidate and self._get_provider(candidate) == provider and self._model_supported(provider, candidate):
            return candidate
        if preference == "free" and available.get("local") and provider in _LOCAL_PROVIDERS:
            return available.get("local", "")
        return ""

    def _provider_task_allowed(self, provider: str, model_preference: str, task_hint: str = "") -> bool:
        normalized_provider = self._normalize_provider(provider)
        access = self._resolve_access(normalized_provider)
        if self._prepaid_premium_enabled(normalized_provider, access) and model_preference in {"free", "heavy"}:
            return True
        if access.selected_method not in {"oauth", "workspace_connector", "enterprise_connector"}:
            return True
        if model_preference != "heavy":
            return False
        hint = (task_hint or "").lower()
        coding_tokens = ("code", "coding", "refactor", "debug", "unit_test", "function", "script", "endpoint", "api", "sql", "patch", "repository", "repo")
        reasoning_tokens = ("reason", "reasoning", "analysis", "security", "exploit", "proof", "theorem", "research")
        return any(token in hint for token in (*coding_tokens, *reasoning_tokens))

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
        normalized_provider = self._normalize_provider(provider)
        if normalized_provider not in self._provider_capabilities:
            if normalized_provider in _LOCAL_PROVIDERS:
                return model_id.lower().startswith(f"{normalized_provider}/")
            if not self._provider_access_layer:
                return True
            access = self._resolve_access(normalized_provider)
            trusted_models = set()
            if access.preferred_model:
                trusted_models.add(str(access.preferred_model).strip())
            if access.target_model:
                trusted_models.add(str(access.target_model).strip())
            trusted_models.update(
                str(item).strip()
                for item in (access.verified_models or [])
                if str(item).strip()
            )
            trusted_models.update(
                str(item).strip()
                for item in (_VERIFIED_DIRECT_MODELS.get(normalized_provider, {}) or {}).values()
                if str(item).strip()
            )
            aliases = self._normalize_model_aliases(provider, model_id)
            return any(
                alias in self._normalize_model_aliases(normalized_provider, trusted_model)
                for trusted_model in trusted_models
                for alias in aliases
            )
        capabilities = self._provider_capabilities.get(normalized_provider) or set()
        if not capabilities:
            return normalized_provider in _LOCAL_PROVIDERS and model_id.lower().startswith(f"{normalized_provider}/")
        aliases = self._normalize_model_aliases(provider, model_id)
        return any(alias in capabilities for alias in aliases)

    def _resolve_access(self, provider: str) -> _AccessSnapshot:
        normalized_provider = self._normalize_provider(provider)
        if self._provider_access_layer:
            decision = self._provider_access_layer.resolve(normalized_provider)
            return _AccessSnapshot(
                provider=normalized_provider,
                selected_method=getattr(decision, "selected_method", "disabled"),
                status=getattr(decision, "status", "unknown"),
                runtime_confirmed=bool(getattr(decision, "runtime_confirmed", False)),
                target_form_reached=bool(getattr(decision, "target_form_reached", False)),
                quota_visibility=getattr(decision, "quota_visibility", ""),
                billing_basis=getattr(decision, "billing_basis", ""),
                preferred_model=str(getattr(decision, "preferred_model", "") or ""),
                verified_models=list(getattr(decision, "verified_models", []) or []),
                target_model=str(getattr(decision, "target_model", "") or ""),
            )
        if normalized_provider in _LOCAL_PROVIDERS:
            return _AccessSnapshot(provider=normalized_provider, selected_method="local", quota_visibility="local_only", billing_basis="local_inference_hardware")
        if normalized_provider in _DIRECT_PROVIDERS or normalized_provider == "openrouter":
            return _AccessSnapshot(provider=normalized_provider, selected_method="api_key", quota_visibility="provider_console", billing_basis="metered_api_billing")
        return _AccessSnapshot(provider=normalized_provider, selected_method="disabled", status="disabled", runtime_confirmed=False, target_form_reached=False)

    def _access_candidate_allowed(self, provider: str, access: _AccessSnapshot) -> bool:
        if access.selected_method == "disabled":
            return False
        if provider in _LOCAL_PROVIDERS:
            return access.runtime_confirmed or access.status in {"configured", "runtime_confirmed"}
        if access.status == "target_form_not_reached" and not access.runtime_confirmed:
            return False
        return True

    def _candidate_has_budget(self, provider: str, balance_report, access: _AccessSnapshot, pricing: ModelPricing) -> bool:
        if provider in _LOCAL_PROVIDERS:
            return access.runtime_confirmed or access.status in {"configured", "runtime_confirmed"}
        if access.selected_method in _ZERO_MARGINAL_METHODS and access.runtime_confirmed:
            return True
        if pricing.is_free and provider != "openrouter":
            return True
        balance = balance_report.balances.get(provider)
        if balance is None:
            return provider != "openrouter"
        return balance.has_credits

    def _runtime_zero_marginal_providers(self, available_models: dict) -> set[str]:
        providers = {
            self._normalize_provider(self._get_provider(model_id))
            for model_id in available_models.values()
            if model_id
        }
        if self._provider_access_layer:
            try:
                summary = self._provider_access_layer.summary()
            except Exception:
                summary = {}
            for provider, decision in (summary or {}).items():
                if (
                    str(decision.get("selected_method") or "") in _ZERO_MARGINAL_METHODS
                    and bool(decision.get("runtime_confirmed"))
                ):
                    providers.add(self._normalize_provider(provider))
        return providers

    def _has_zero_marginal_runtime_path(self, available_models: dict, exclude_providers: set[str], exclude_models: set[str]) -> bool:
        for model_id in available_models.values():
            if not model_id or model_id in exclude_models:
                continue
            provider = self._normalize_provider(self._get_provider(model_id))
            if provider in exclude_providers:
                continue
            access = self._resolve_access(provider)
            if provider in _LOCAL_PROVIDERS and access.runtime_confirmed:
                return True
            if access.selected_method in _ZERO_MARGINAL_METHODS and access.runtime_confirmed:
                return True
        if self._provider_access_layer:
            for provider in self._runtime_zero_marginal_providers(available_models):
                if provider in exclude_providers:
                    continue
                access = self._resolve_access(provider)
                if access.selected_method in _ZERO_MARGINAL_METHODS and access.runtime_confirmed:
                    return True
        return False

    def _is_verified_balance(self, balance) -> bool:
        if not balance or not balance.has_credits:
            return False
        if balance.balance_usd > 0:
            return True
        if balance.is_free_tier:
            return True
        return balance.source == "api"

    def _provider_rank(self, provider: str, balance_report, access: _AccessSnapshot | None = None) -> int:
        access = access or self._resolve_access(provider)
        if provider in _LOCAL_PROVIDERS and access.runtime_confirmed:
            return 5
        if access.selected_method in _ZERO_MARGINAL_METHODS and access.runtime_confirmed and not balance_report.balances.get(provider):
            return 20 if access.selected_method == "oauth" else 15

        balance = balance_report.balances.get(provider)
        if not balance or not balance.has_credits:
            if access.runtime_confirmed:
                return 70
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
        pricing = self._pricing_for_candidate(model_id, self._get_provider(model_id))
        return (pricing.input_cost_per_1k + pricing.output_cost_per_1k) * tokens / 1000

    def _pricing_for_candidate(self, model_id: str, provider: str) -> ModelPricing:
        pricing = self._pricing.get(model_id)
        if pricing:
            return pricing
        base_cost = _estimate_base_cost_per_1k(provider, model_id)
        return ModelPricing(
            model_id=model_id,
            provider=provider,
            input_cost_per_1k=base_cost,
            output_cost_per_1k=base_cost * 0.5,
            is_free=provider in _LOCAL_PROVIDERS or model_id.endswith(":free"),
            quality_score=_estimate_quality(model_id),
            speed_score=_estimate_speed(model_id),
            stability_score=_estimate_stability(model_id),
        )

    def _estimate_effective_cost(self, model_id: str, provider: str, tokens: int, access: _AccessSnapshot, balance_report, pricing: ModelPricing) -> float:
        if provider in _LOCAL_PROVIDERS:
            return 0.0
        if pricing.is_free and provider != "openrouter":
            return 0.0
        if access.selected_method in _ZERO_MARGINAL_METHODS and access.runtime_confirmed:
            return 0.0
        balance = balance_report.balances.get(provider)
        if balance and (balance.is_free_tier or (balance.is_subscription and self._is_verified_balance(balance))):
            return 0.0
        return (pricing.input_cost_per_1k + pricing.output_cost_per_1k) * tokens / 1000

    def _preference_allows_candidate(self, model_preference: str, model_id: str, pricing: ModelPricing, available_models: dict) -> bool:
        if not model_preference:
            return True
        mid = model_id.lower()
        if model_preference == "visual":
            preferred = available_models.get("visual", "")
            return model_id == preferred or any(token in mid for token in ("vision", "gpt-4o", "-vl", "/vl", "gemini"))
        if model_preference == "heavy":
            preferred = available_models.get("heavy", "")
            return model_id == preferred or pricing.quality_score >= 0.68
        return True

    def _preference_fit_score(self, model_preference: str, model_id: str, pricing: ModelPricing, available_models: dict, estimated_cost_usd: float) -> float:
        preferred_model = available_models.get(model_preference, "")
        if model_id and preferred_model and model_id == preferred_model:
            return 100.0
        if model_preference == "visual":
            mid = model_id.lower()
            return 92.0 if any(token in mid for token in ("vision", "gpt-4o", "-vl", "/vl", "gemini")) else 35.0
        if model_preference == "heavy":
            if pricing.quality_score >= 0.90:
                return 95.0
            if pricing.quality_score >= 0.80:
                return 82.0
            if pricing.quality_score >= 0.68:
                return 62.0
            return 30.0
        if model_preference == "local":
            return 100.0 if self._get_provider(model_id) in _LOCAL_PROVIDERS else 25.0
        if estimated_cost_usd <= 0:
            return 92.0
        if pricing.is_free:
            return 95.0
        return 48.0

    def _cost_efficiency_score(self, estimated_cost_usd: float) -> float:
        if estimated_cost_usd <= 0:
            return 100.0
        if estimated_cost_usd <= 0.002:
            return 92.0
        if estimated_cost_usd <= 0.01:
            return 80.0
        if estimated_cost_usd <= 0.02:
            return 68.0
        if estimated_cost_usd <= 0.05:
            return 52.0
        if estimated_cost_usd <= 0.10:
            return 38.0
        return max(10.0, 38.0 - min(28.0, estimated_cost_usd * 120.0))

    def _access_reliability_score(self, provider: str, access: _AccessSnapshot) -> float:
        base = {
            "local": 100.0,
            "api_key": 88.0,
            "oauth": 80.0,
            "workspace_connector": 78.0,
            "enterprise_connector": 82.0,
            "disabled": 0.0,
        }.get(access.selected_method, 60.0)
        if access.runtime_confirmed:
            base += 6.0
        if access.target_form_reached:
            base += 4.0

        qv = (access.quota_visibility or "").lower()
        if any(token in qv for token in ("machine_readable", "api_balance", "local_only")):
            base += 6.0
        elif "provider_console" in qv or qv == "partial":
            base += 2.0
        elif any(token in qv for token in ("ui_only", "not_fully_machine_readable")):
            base -= 4.0

        if provider == "openrouter":
            base -= 20.0
        return max(0.0, min(base, 100.0))

    def _rank_score(self, global_rank: int) -> float:
        if global_rank <= 0 or global_rank >= 9999:
            return 55.0
        return max(35.0, 100.0 - min(65.0, (global_rank - 1) * 0.8))

    def _local_profile(self, model_id: str) -> dict:
        return self._local_profiles.get(model_id, {})

    def _local_profile_speed_score(self, model_id: str, fallback_speed: float) -> float:
        profile = self._local_profile(model_id)
        score = profile.get('speed_score')
        if isinstance(score, (int, float)):
            return max(0.0, min(float(score), 100.0))
        return fallback_speed

    def _local_profile_stability_score(self, model_id: str, fallback_stability: float) -> float:
        profile = self._local_profile(model_id)
        score = profile.get('stability_score')
        if isinstance(score, (int, float)):
            return max(0.0, min(float(score), 100.0))
        return fallback_stability

    def _task_profile_keys(self, model_preference: str, task_hint: str = "") -> list[str]:
        hint = (task_hint or "").lower()
        keys: list[str] = []
        if any(token in hint for token in ("json", "schema", "structured", "extract", "yaml", "xml", "csv")):
            keys.append("structured_output")
        if any(token in hint for token in ("code", "coding", "refactor", "debug", "endpoint", "function", "script", "unit_test", "api", "sql")):
            keys.append("coding")
        if any(token in hint for token in ("reason", "reasoning", "proof", "theorem", "math", "research", "analysis", "security", "exploit", "deep")):
            keys.append("reasoning")

        if not keys:
            if model_preference == "visual":
                keys.append("general_chat")
            elif model_preference == "heavy":
                keys.extend(["reasoning", "coding"])
            else:
                keys.append("general_chat")

        ordered: list[str] = []
        for key in keys:
            if key and key not in ordered:
                ordered.append(key)
        return ordered or ["general_chat"]

    def _local_profile_task_metrics(self, model_id: str, model_preference: str, task_hint: str = "") -> tuple[list[str], float | None, float | None]:
        profile = self._local_profile(model_id)
        keys = self._task_profile_keys(model_preference, task_hint)
        if not profile:
            return keys, None, None
        suitability = profile.get('prompt_type_suitability') or {}
        task_profiles = profile.get('task_profiles') or {}
        suitability_values = [float(suitability[key]) for key in keys if isinstance(suitability.get(key), (int, float))]
        success_values = []
        for key in keys:
            task_profile = task_profiles.get(key) or {}
            if isinstance(task_profile.get('success'), bool):
                success_values.append(100.0 if task_profile.get('success') else 15.0)
        suitability_score = (sum(suitability_values) / len(suitability_values)) if suitability_values else None
        success_score = (sum(success_values) / len(success_values)) if success_values else None
        return keys, suitability_score, success_score

    def _local_task_thresholds(self, keys: list[str]) -> tuple[float, float]:
        min_suitability = 80.0
        min_success = 75.0
        if "coding" in keys:
            min_suitability = max(min_suitability, 85.0)
            min_success = max(min_success, 80.0)
        if "reasoning" in keys:
            min_suitability = max(min_suitability, 90.0)
            min_success = max(min_success, 90.0)
        if "structured_output" in keys:
            min_suitability = max(min_suitability, 92.0)
            min_success = max(min_success, 95.0)
        return min_suitability, min_success

    def _local_profile_fit_score(self, model_preference: str, model_id: str, fallback_fit: float, task_hint: str = "") -> float:
        profile = self._local_profile(model_id)
        if not profile:
            return fallback_fit
        keys, suitability_score, success_score = self._local_profile_task_metrics(model_id, model_preference, task_hint)
        if suitability_score is None:
            return fallback_fit
        if success_score is None:
            success_score = float(profile.get('success_rate') or 0.0) * 100.0
        min_suitability, min_success = self._local_task_thresholds(keys)
        blended = (fallback_fit * 0.20) + (suitability_score * 0.60) + (success_score * 0.20)
        if suitability_score < min_suitability:
            blended -= 18.0
        elif suitability_score < (min_suitability + 8.0):
            blended -= 10.0
        if success_score < min_success:
            blended -= 12.0
        return max(5.0, round(blended, 4))

    def _local_profile_access_score(self, provider: str, access: _AccessSnapshot, model_id: str, fallback_access: float, model_preference: str, task_hint: str = "") -> float:
        if provider not in _LOCAL_PROVIDERS:
            return fallback_access
        profile = self._local_profile(model_id)
        if not profile:
            return max(45.0, fallback_access - 12.0)
        _, suitability_score, task_success_score = self._local_profile_task_metrics(model_id, model_preference, task_hint)
        success_rate = float(profile.get('success_rate') or 0.0) * 100.0
        effective_success = task_success_score if task_success_score is not None else success_rate
        runtime_bonus = 6.0 if profile.get('runtime_confirmed') else -8.0
        capacity_penalty = 0.0
        if profile.get('capacity_status') == 'machine_capacity_blocked':
            capacity_penalty = 20.0
        elif profile.get('capacity_status') == 'capacity_estimate_conflict':
            capacity_penalty = 8.0
        suitability_penalty = 10.0 if suitability_score is not None and suitability_score < 40.0 else 0.0
        return max(12.0, min(100.0, (fallback_access * 0.20) + (success_rate * 0.25) + (effective_success * 0.55) + runtime_bonus - capacity_penalty - suitability_penalty))

    def _local_candidate_is_viable_for_task(
        self,
        model_id: str,
        model_preference: str,
        task_hint: str = "",
        estimated_tokens: int | None = None,
    ) -> bool:
        profile = self._local_profile(model_id)
        if not profile:
            return False
        if not profile.get('runtime_confirmed'):
            return False
        if estimated_tokens is not None and estimated_tokens > 3000:
            return False
        if model_preference == "local":
            return True
        keys, suitability_score, task_success_score = self._local_profile_task_metrics(model_id, model_preference, task_hint)
        if suitability_score is None:
            return False
        min_suitability, min_success = self._local_task_thresholds(keys)
        if suitability_score < min_suitability:
            return False
        if task_success_score is not None and task_success_score < min_success:
            return False
        return True

    def _normalize_user_routing_preference(self, value: str) -> str:
        normalized = str(value or "balanced").strip().lower()
        if normalized in {"balanced", "max_intelligence", "min_cost", "prefer_local"}:
            return normalized
        return "balanced"

    def _select_user_preferred_local_route(
        self,
        model_preference: str,
        available_models: dict,
        estimated_tokens: int,
        balance_report,
        exclude_providers: set[str],
        exclude_models: set[str],
        task_hint: str = "",
    ) -> CostRoute | None:
        model_id = available_models.get("local", "")
        if not model_id or model_id in exclude_models:
            return None
        provider = self._normalize_provider(self._get_provider(model_id))
        if provider in exclude_providers:
            return None
        access = self._resolve_access(provider)
        if access.selected_method not in _ZERO_MARGINAL_METHODS or not access.runtime_confirmed:
            return None
        if not self._local_candidate_is_viable_for_task(model_id, model_preference, task_hint, estimated_tokens):
            return None
        return self._build_route(
            model_id=model_id,
            model_preference="local",
            available_models=available_models,
            balance_report=balance_report,
            estimated_tokens=estimated_tokens,
            reason="user_prefer_local",
            task_hint=task_hint,
        )

    def _local_effective_score(
        self,
        pricing: ModelPricing,
        model_preference: str,
        model_id: str,
        provider: str,
        access: _AccessSnapshot,
        estimated_cost_usd: float,
        available_models: dict,
        task_hint: str = "",
    ) -> float:
        quality = max(0.0, min(pricing.quality_score, 1.0)) * 100.0
        speed = max(0.0, min(pricing.speed_score, 1.0)) * 100.0
        stability = max(0.0, min(pricing.stability_score, 1.0)) * 100.0
        cost_eff = self._cost_efficiency_score(estimated_cost_usd)
        access_rel = self._access_reliability_score(provider, access)
        fit = self._preference_fit_score(model_preference, model_id, pricing, available_models, estimated_cost_usd)
        if provider in _LOCAL_PROVIDERS:
            speed = self._local_profile_speed_score(model_id, speed)
            stability = self._local_profile_stability_score(model_id, stability)
            fit = self._local_profile_fit_score(model_preference, model_id, fit, task_hint)
            access_rel = self._local_profile_access_score(provider, access, model_id, access_rel, model_preference, task_hint)
        rank = self._rank_score(pricing.global_rank)
        return round(
            quality * 0.45
            + speed * 0.12
            + stability * 0.08
            + cost_eff * 0.10
            + access_rel * 0.10
            + fit * 0.10
            + rank * 0.05,
            4,
        )

    def _tier_for_candidate(self, provider: str, access: _AccessSnapshot, pricing: ModelPricing, estimated_cost_usd: float, balance_report) -> str:
        if provider in _LOCAL_PROVIDERS or access.selected_method == "local":
            return "local"
        if access.selected_method == "oauth" and estimated_cost_usd <= 0:
            return "oauth_window"
        if access.selected_method in {"workspace_connector", "enterprise_connector"} and estimated_cost_usd <= 0:
            return "connector"
        balance = balance_report.balances.get(provider)
        if estimated_cost_usd <= 0 and balance and balance.is_subscription:
            return "subscription"
        if estimated_cost_usd <= 0 or pricing.is_free or (balance and balance.is_free_tier):
            return "free"
        return "pay-per-token"

    def _select_runtime_zero_marginal_route(
        self,
        model_preference: str,
        available_models: dict,
        estimated_tokens: int,
        balance_report,
        exclude_providers: set[str],
        exclude_models: set[str],
        task_hint: str = "",
    ) -> CostRoute | None:
        candidate_providers = self._runtime_zero_marginal_providers(available_models)

        candidates = []
        allow_local_runtime = model_preference == "local" or not bool(balance_report.providers_with_credits)
        for provider in sorted(candidate_providers):
            if provider in exclude_providers:
                continue
            access = self._resolve_access(provider)
            if access.selected_method not in _ZERO_MARGINAL_METHODS or not access.runtime_confirmed:
                continue
            if not self._provider_task_allowed(provider, model_preference, task_hint):
                continue
            if provider in _LOCAL_PROVIDERS:
                if not allow_local_runtime:
                    continue
                model_id = available_models.get("local", "")
                if model_id and not self._local_candidate_is_viable_for_task(model_id, model_preference, task_hint, estimated_tokens):
                    continue
            else:
                model_id = self._find_verified_direct_fallback_model(provider, model_preference, available_models, access)
            if not model_id or model_id in exclude_models or not self._model_supported(provider, model_id):
                continue
            pricing = self._pricing_for_candidate(model_id, provider)
            est_cost = self._estimate_effective_cost(model_id, provider, estimated_tokens, access, balance_report, pricing)
            local_score = self._local_effective_score(
                pricing=pricing,
                model_preference=model_preference,
                model_id=model_id,
                provider=provider,
                access=access,
                estimated_cost_usd=est_cost,
                available_models=available_models,
                task_hint=task_hint,
            )
            candidates.append((
                -local_score,
                self._provider_rank(provider, balance_report, access),
                self._estimate_cost(model_id, estimated_tokens),
                provider,
                model_id,
                access,
            ))

        if not candidates:
            return None

        candidates.sort()
        _, _, _, provider, model_id, access = candidates[0]
        return self._build_route(
            model_id=model_id,
            model_preference=model_preference,
            available_models=available_models,
            balance_report=balance_report,
            estimated_tokens=estimated_tokens,
            reason=f"{access.selected_method}_access:{provider}",
            task_hint=task_hint,
        )

    def _select_prepaid_premium_route(
        self,
        model_preference: str,
        available_models: dict,
        estimated_tokens: int,
        balance_report,
        exclude_providers: set[str],
        exclude_models: set[str],
        task_hint: str = "",
    ) -> CostRoute | None:
        if model_preference == "visual":
            return None
        if not self._routing_preferences.get("prefer_prepaid_premium"):
            return None

        candidates = []
        configured_providers = set(self._routing_preferences.get("prepaid_premium_providers", set()))
        providers = set(configured_providers)
        if self._provider_access_layer:
            providers.update(self._normalize_provider(provider) for provider in self._provider_access_layer.summary().keys())

        for provider in sorted(providers):
            if provider in exclude_providers:
                continue
            access = self._resolve_access(provider)
            if not self._prepaid_premium_enabled(provider, access):
                continue
            if not self._provider_task_allowed(provider, model_preference, task_hint):
                continue
            model_id = self._find_verified_direct_fallback_model(provider, model_preference, available_models, access)
            if not model_id or model_id in exclude_models:
                continue
            if not self._model_supported(provider, model_id):
                continue
            pricing = self._pricing_for_candidate(model_id, provider)
            est_cost = self._estimate_effective_cost(model_id, provider, estimated_tokens, access, balance_report, pricing)
            local_score = self._local_effective_score(
                pricing=pricing,
                model_preference=model_preference,
                model_id=model_id,
                provider=provider,
                access=access,
                estimated_cost_usd=est_cost,
                available_models=available_models,
                task_hint=task_hint,
            )
            candidates.append((
                -local_score,
                self._provider_rank(provider, balance_report, access),
                provider,
                model_id,
            ))

        if not candidates:
            return None

        candidates.sort()
        _, _, provider, model_id = candidates[0]
        return self._build_route(
            model_id=model_id,
            model_preference=model_preference,
            available_models=available_models,
            balance_report=balance_report,
            estimated_tokens=estimated_tokens,
            reason=f"prepaid_premium_preference:{provider}",
            task_hint=task_hint,
        )

    def _prepaid_premium_enabled(self, provider: str, access: _AccessSnapshot | None = None) -> bool:
        if not bool(self._routing_preferences.get("prefer_prepaid_premium")):
            return False
        normalized = self._normalize_provider(provider)
        access = access or self._resolve_access(normalized)
        if access.selected_method not in {"oauth", "workspace_connector", "enterprise_connector"}:
            return False
        if not access.runtime_confirmed:
            return False
        configured = self._routing_preferences.get("prepaid_premium_providers", set())
        if normalized in configured:
            return True
        billing_basis = (access.billing_basis or "").lower()
        billing_tokens = ("subscription", "entitlement", "workspace", "enterprise")
        return any(token in billing_basis for token in billing_tokens)

    def _model_is_credible(self, model_id: str, balance_report) -> bool:
        provider = self._get_provider(model_id)
        access = self._resolve_access(provider)
        if not self._model_supported(provider, model_id):
            return False
        if not self._access_candidate_allowed(provider, access):
            return False
        pricing = self._pricing_for_candidate(model_id, provider)
        if provider in _LOCAL_PROVIDERS:
            return access.runtime_confirmed
        balance = balance_report.balances.get(provider)
        if provider == "openrouter":
            if not balance:
                return False
            return balance.has_credits and (balance.balance_usd > 0 or balance.source == "api")
        if access.selected_method in _ZERO_MARGINAL_METHODS and access.runtime_confirmed:
            return True
        if balance is None:
            return True
        return self._candidate_has_budget(provider, balance_report, access, pricing) and self._is_verified_balance(balance)

    def _build_route(
        self,
        model_id: str,
        model_preference: str,
        available_models: dict,
        balance_report,
        estimated_tokens: int,
        reason: str,
        forced_tier: str = "",
        task_hint: str = "",
    ) -> CostRoute:
        provider = self._normalize_provider(self._get_provider(model_id))
        access = self._resolve_access(provider)
        pricing = self._pricing_for_candidate(model_id, provider)
        est_cost = self._estimate_effective_cost(model_id, provider, estimated_tokens, access, balance_report, pricing)
        return CostRoute(
            model=model_id,
            provider=provider,
            estimated_cost_usd=est_cost,
            reason=reason,
            tier=forced_tier or self._tier_for_candidate(provider, access, pricing, est_cost, balance_report),
            local_effective_score=self._local_effective_score(
                pricing=pricing,
                model_preference=model_preference,
                model_id=model_id,
                provider=provider,
                access=access,
                estimated_cost_usd=est_cost,
                available_models=available_models,
                task_hint=task_hint,
            ),
            access_method=access.selected_method,
        )
    def _add_alert(self, alert_type: str, message: str):
        self._budget_alerts.append({"type": alert_type, "message": message})
        log.warning(f"Budget alert [{alert_type}]: {message}")

    @property
    def alerts(self) -> list[dict]:
        return self._budget_alerts

    def clear_alerts(self):
        self._budget_alerts.clear()


def _normalize_metric(value, fallback: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    if num > 1.0:
        return max(0.0, min(num / 100.0, 1.0))
    return max(0.0, min(num, 1.0))


def _estimate_quality(model_id: str) -> float:
    mid = model_id.lower()
    if any(x in mid for x in ("gpt-5.4", "deepseek-v4", "deepseek-v4", "qwen3-235b", "qwen3.5-235b", "opus-4.6")):
        return 0.97
    if any(x in mid for x in ("o3-pro", "gpt-5", "opus", "gemini-3", "deepseek-v4", "qwen3-235b", "reasoner-v4")):
        return 0.95
    if any(x in mid for x in ("gpt-4.1", "sonnet-4", "gemini-2.5-pro", "o4", "qwen3.5-9b", "qwen3.5-14b")):
        return 0.88
    if any(x in mid for x in ("deepseek-r1", "deepseek-reasoner", "claude-3.7", "gemini-2.5-flash", "deepseek-v3")):
        return 0.82
    if any(x in mid for x in ("gpt-4o", "deepseek-chat", "llama-3.3", "qwen3", "kimi")):
        return 0.75
    if any(x in mid for x in ("mini", "flash", "haiku", "nano", "instant")):
        return 0.65
    if ":free" in mid:
        return 0.55
    return 0.70


def _estimate_speed(model_id: str) -> float:
    mid = model_id.lower()
    if any(x in mid for x in ("flash", "instant", "nano", "mini", "lite", "turbo")):
        return 0.93
    if any(x in mid for x in ("gpt-4o", "haiku", "deepseek-chat")):
        return 0.85
    if any(x in mid for x in ("reasoner", "o3-pro", "opus", "thinking", "research")):
        return 0.56
    return 0.72


def _estimate_stability(model_id: str) -> float:
    mid = model_id.lower()
    if any(x in mid for x in ("preview", "experimental", "beta")):
        return 0.60
    if any(x in mid for x in ("gpt-5", "gpt-4.1", "claude", "gemini-2.5", "deepseek-chat", "qwen3.5")):
        return 0.88
    if ":free" in mid:
        return 0.72
    return 0.78



def _estimate_base_cost_per_1k(provider: str, model_id: str) -> float:
    normalized_provider = _PROVIDER_NAME_MAP.get((provider or "").strip().lower(), (provider or "").strip().lower())
    mid = (model_id or "").lower()
    if normalized_provider in _LOCAL_PROVIDERS or mid.endswith(":free"):
        return 0.0
    if normalized_provider == "openrouter":
        if any(token in mid for token in ("reasoner", "o3-pro", "opus", "gemini-2.5-pro", "gpt-5", "gpt-4.1")):
            return 0.03
        return 0.012
    if normalized_provider in {"openai", "openai-codex"}:
        if any(token in mid for token in ("gpt-5", "o3", "reasoner", "codex")):
            return 0.025
        if "mini" in mid or "nano" in mid:
            return 0.006
        return 0.015
    if normalized_provider == "google":
        if "pro" in mid:
            return 0.012
        return 0.004
    if normalized_provider == "anthropic":
        if "haiku" in mid:
            return 0.004
        return 0.015
    if normalized_provider == "groq":
        return 0.003
    if normalized_provider == "deepseek":
        if "reasoner" in mid:
            return 0.007
        return 0.002
    return 0.01
