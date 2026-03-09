from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..constants import BENCHMARK_MAP, OAUTH_BRIDGES


def _normalize_alias(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("_", " ").replace("/", " ").replace(":", " ")
    value = re.sub(r"[^a-z0-9+.\-\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def family_id_from_model_id(model_id: str) -> str:
    model_id = (model_id or "").strip().lower()
    if not model_id:
        return ""
    if "/" not in model_id:
        return model_id
    provider, name = model_id.split("/", 1)
    base_name = name.split(":", 1)[0]
    return f"{provider}/{base_name}"


_MANUAL_ALIASES = {
    "gpt 4o": "openai/gpt-4o",
    "gpt4o": "openai/gpt-4o",
    "gpt 4.1": "openai/gpt-4.1",
    "gpt 4 1": "openai/gpt-4.1",
    "o3 pro": "openai/o3-pro",
    "o4 mini": "openai/o4-mini",
    "gemini 2.5 flash": "google/gemini-2.5-flash",
    "gemini 2.5 pro": "google/gemini-2.5-pro",
    "claude sonnet 4": "anthropic/claude-sonnet-4",
    "claude sonnet 4.6": "anthropic/claude-sonnet-4.6",
    "claude opus 4.6": "anthropic/claude-opus-4.6",
    "deepseek r1": "deepseek/deepseek-r1",
    "deepseek chat": "deepseek/deepseek-chat",
    "qwen max": "qwen/qwen-max",
}


@dataclass
class AliasRegistry:
    alias_to_family: dict[str, str] = field(default_factory=dict)
    family_to_catalog_ids: dict[str, list[str]] = field(default_factory=dict)

    def register(self, alias: str, family_id: str, catalog_id: str | None = None) -> None:
        key = _normalize_alias(alias)
        family_id = family_id_from_model_id(family_id)
        if not key or not family_id:
            return
        existing = self.alias_to_family.get(key)
        if existing is None or family_id < existing:
            self.alias_to_family[key] = family_id
        if catalog_id:
            catalog_list = self.family_to_catalog_ids.setdefault(family_id, [])
            if catalog_id not in catalog_list:
                catalog_list.append(catalog_id)
                catalog_list.sort()

    def resolve(self, raw_name: str) -> str | None:
        key = _normalize_alias(raw_name)
        if not key:
            return None
        if key in self.alias_to_family:
            return self.alias_to_family[key]

        collapsed = key.replace(" ", "")
        for alias, family_id in sorted(self.alias_to_family.items()):
            alias_collapsed = alias.replace(" ", "")
            if alias_collapsed == collapsed:
                return family_id
        for alias, family_id in sorted(self.alias_to_family.items()):
            if key in alias or alias in key:
                return family_id
        return None

    def candidate_catalog_ids(self, family_id: str) -> list[str]:
        return list(self.family_to_catalog_ids.get(family_id_from_model_id(family_id), []))

    def known_families(self) -> list[str]:
        return sorted(self.family_to_catalog_ids)


def build_alias_registry(openrouter_models: list[dict]) -> AliasRegistry:
    registry = AliasRegistry()

    for model_id in sorted(BENCHMARK_MAP):
        family_id = family_id_from_model_id(model_id)
        registry.register(model_id, family_id)
        if "/" in model_id:
            _, slug = model_id.split("/", 1)
            registry.register(slug, family_id)

    for model_id in sorted(OAUTH_BRIDGES):
        family_id = family_id_from_model_id(model_id)
        registry.register(model_id, family_id, model_id)

    for alias, family_id in sorted(_MANUAL_ALIASES.items()):
        registry.register(alias, family_id)

    for model in sorted(openrouter_models, key=lambda item: str(item.get("id", ""))):
        model_id = str(model.get("id", "")).strip().lower()
        if not model_id:
            continue
        family_id = family_id_from_model_id(model_id)
        name = str(model.get("name", "")).strip()
        registry.register(model_id, family_id, model_id)
        registry.register(family_id, family_id, model_id)
        if "/" in model_id:
            _, slug = model_id.split("/", 1)
            registry.register(slug, family_id, model_id)
            registry.register(slug.replace("-", " "), family_id, model_id)
        if name:
            registry.register(name, family_id, model_id)
            registry.register(name.replace("-", " "), family_id, model_id)
    return registry
