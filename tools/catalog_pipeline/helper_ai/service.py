from __future__ import annotations

import json
from typing import Any

from ..constants import MAX_HELPER_ALIAS_BATCH, MAX_HELPER_TASK_ENRICH_MODELS, SUPPORTED_TASK_TYPES
from ..types import HelperProviderStatus, HelperResolutionReport
from .providers import GeminiHelperProvider, GroqHelperProvider


class AIHelperService:
    def __init__(self, primary: GeminiHelperProvider | None = None, fallback: GroqHelperProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        provider_statuses: dict[str, HelperProviderStatus] = {}
        for role, provider in (("primary", primary), ("fallback", fallback)):
            if provider is None:
                continue
            configured = bool(getattr(provider, "api_key", ""))
            provider_statuses[provider.name] = HelperProviderStatus(
                name=provider.name,
                role=role,
                configured=configured,
                available=provider.available,
                runtime_confirmed=False,
                status="configured_not_confirmed" if provider.available else "blocked_missing_credentials",
                blocked_reason=None if provider.available else "missing api key",
            )
        self.report = HelperResolutionReport(
            available_helpers=[provider.name for provider in (primary, fallback) if provider is not None and provider.available],
            provider_statuses=provider_statuses,
        )

    def _mark_result(self, provider_name: str, *, ok: bool, error: str | None = None, fallback_used: bool = False) -> None:
        status = self.report.provider_statuses.get(provider_name)
        if status is None:
            return
        status.invocation_count += 1
        if ok:
            status.successes += 1
            status.runtime_confirmed = True
            status.status = "runtime_confirmed"
            status.used_as_fallback = fallback_used
            status.last_error = None
        else:
            status.failures += 1
            status.last_error = error
            if status.available:
                status.status = "degraded"

    def _call_with_failover(self, prompt: str) -> dict[str, Any] | list[Any] | None:
        providers = [provider for provider in (self.primary, self.fallback) if provider is not None and provider.available]
        for index, provider in enumerate(providers):
            result = provider.call_json(prompt)
            if result.ok:
                self._mark_result(provider.name, ok=True, fallback_used=index > 0)
                self.report.used_provider = provider.name
                self.report.fallback_used = index > 0
                self.report.events.append(f"{provider.name}:success")
                return result.payload
            self._mark_result(provider.name, ok=False, error=result.error)
            self.report.failed_helpers.append(provider.name)
            self.report.events.append(f"{provider.name}:failed:{result.error}")
        return None

    def prioritize_free_models(self, candidates: list[dict[str, Any]]) -> list[str]:
        if not candidates:
            return []
        prompt = (
            "Return JSON object with key 'top_models' as an array of exact model ids. "
            "Pick up to 5 free or subscription-covered models that best balance intelligence, speed, stability, and context.\n"
            f"Candidates: {json.dumps(candidates[:40], ensure_ascii=False)}"
        )
        payload = self._call_with_failover(prompt)
        if isinstance(payload, dict):
            models = payload.get("top_models", [])
            if isinstance(models, list):
                return [str(item) for item in models if isinstance(item, str)]
        if isinstance(payload, list):
            return [str(item) for item in payload if isinstance(item, str)]
        return []

    def resolve_aliases(self, aliases: list[str], known_families: list[str]) -> dict[str, str]:
        aliases = aliases[:MAX_HELPER_ALIAS_BATCH]
        if not aliases:
            return {}
        prompt = (
            "Return JSON object with key 'mappings'. Each mapping key is an unresolved source model name and each value is the "
            "best matching canonical family id from the allowed list. If none matches, omit it.\n"
            f"Allowed families: {json.dumps(known_families[:200], ensure_ascii=False)}\n"
            f"Unresolved aliases: {json.dumps(aliases, ensure_ascii=False)}"
        )
        payload = self._call_with_failover(prompt)
        if not isinstance(payload, dict):
            return {}
        mappings = payload.get("mappings", {})
        if not isinstance(mappings, dict):
            return {}
        return {str(k): str(v) for k, v in mappings.items() if isinstance(k, str) and isinstance(v, str)}

    def enrich_tasks(self, model_cards: list[dict[str, Any]]) -> dict[str, list[str]]:
        model_cards = model_cards[:MAX_HELPER_TASK_ENRICH_MODELS]
        if not model_cards:
            return {}
        prompt = (
            "Return JSON object with key 'tasks'. Each value must be an array containing only these task labels: "
            f"{', '.join(SUPPORTED_TASK_TYPES)}. Use them only when strongly justified.\n"
            f"Models: {json.dumps(model_cards, ensure_ascii=False)}"
        )
        payload = self._call_with_failover(prompt)
        if not isinstance(payload, dict):
            return {}
        tasks = payload.get("tasks", {})
        if not isinstance(tasks, dict):
            return {}
        result: dict[str, list[str]] = {}
        for model_id, labels in tasks.items():
            if isinstance(model_id, str) and isinstance(labels, list):
                result[model_id] = [label for label in labels if label in SUPPORTED_TASK_TYPES]
        return result

    def to_dict(self) -> dict[str, Any]:
        return self.report.to_dict()


def build_helper_service(gemini_key: str | None, groq_key: str | None) -> AIHelperService:
    return AIHelperService(
        primary=GeminiHelperProvider(gemini_key),
        fallback=GroqHelperProvider(groq_key),
    )
