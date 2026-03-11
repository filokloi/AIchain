from __future__ import annotations

import math
import re
from copy import deepcopy

from ..constants import BENCHMARK_MAP, MAJOR_PROVIDER_PREFIXES, OAUTH_BRIDGES
from ..rank.tasks import infer_task_metadata
from ..self_hosting import derive_self_hosting_profile
from ..types import MergeDiagnostics
from .aliases import AliasRegistry, family_id_from_model_id

_FIELD_SOURCE_PRIORITY = {
    "intelligence": ("benchmark", "artificial_analysis", "lmsys", "helper", "heuristic"),
    "speed": ("artificial_analysis", "openrouter", "heuristic"),
    "context_length": ("openrouter", "bridge"),
    "pricing": ("openrouter", "bridge"),
}


def _determine_provider(model_id: str) -> str:
    if "/" not in model_id:
        return "Unknown"
    return model_id.split("/", 1)[0].replace("-", " ").title()


def _parse_cost(pricing: dict | None) -> tuple[float, float, float]:
    if not pricing:
        return 0.0, 0.0, 0.0
    try:
        prompt = float(pricing.get("prompt", "0") or 0.0)
        completion = float(pricing.get("completion", "0") or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0
    return prompt, completion, (prompt + completion) / 2.0


def _context_length(model: dict) -> int:
    try:
        return int(model.get("context_length") or 4096)
    except (TypeError, ValueError):
        return 4096


def _normalize_elo_to_intelligence(elo_value: float) -> int:
    return max(0, min(100, int((elo_value - 800) / 6)))


def _heuristic_intelligence(model_id: str, context_length: int) -> int:
    lower = model_id.lower()
    base_score = 65
    if any(token in lower for token in ("pro", "opus", "-max", "large")):
        base_score += 18
    elif any(token in lower for token in ("sonnet", "plus", "medium")):
        base_score += 15
    elif any(token in lower for token in ("flash", "mini", "haiku", "small", "nano", "lite")):
        base_score += 12
    if any(token in lower for token in ("thinking", "reason", "r1", "o1", "o3", "qwq")):
        base_score += 6
    param_match = re.search(r"(\d+(?:\.\d+)?)\s*[bm]", lower.replace("-", ""))
    if param_match:
        value = float(param_match.group(1))
        if "m" in param_match.group(0):
            value = value / 1000.0
        if value >= 400:
            param_score = 93
        elif value >= 100:
            param_score = 89
        elif value >= 65:
            param_score = 87
        elif value >= 30:
            param_score = 83
        elif value >= 12:
            param_score = 77
        elif value >= 7:
            param_score = 73
        elif value >= 3:
            param_score = 66
        else:
            param_score = 60
        base_score = param_score + (base_score - 65) * 0.5
    ctx_boost = max(0, min(3, math.log2(max(context_length / 4096, 1))))
    return min(int(base_score + ctx_boost), 95)


def _estimate_stability(model_id: str, model: dict, source_count: int) -> int:
    top = model.get("top_provider", {}) if isinstance(model.get("top_provider"), dict) else {}
    score = 68
    if top.get("is_moderated"):
        score += 10
    context_length = _context_length(model)
    if context_length >= 128000:
        score += 10
    elif context_length >= 32000:
        score += 5
    if any(model_id.startswith(prefix) for prefix in MAJOR_PROVIDER_PREFIXES):
        score += 8
    if source_count >= 2:
        score += 5
    return min(score, 99)


def _estimate_availability(model_id: str, source_count: int, model: dict) -> int:
    top = model.get("top_provider", {}) if isinstance(model.get("top_provider"), dict) else {}
    score = 60
    if model.get("id"):
        score += 15
    if any(model_id.startswith(prefix) for prefix in MAJOR_PROVIDER_PREFIXES):
        score += 10
    if source_count >= 2:
        score += 8
    if top.get("is_moderated"):
        score += 4
    if model_id in OAUTH_BRIDGES:
        score += 3
    return min(score, 98)


def _top_provider_speed_hint(model: dict) -> float | None:
    top = model.get("top_provider", {}) if isinstance(model.get("top_provider"), dict) else {}
    value = top.get("max_completion_tokens")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _dedupe_catalog(models: list[dict]) -> tuple[list[dict], int]:
    deduped: dict[str, dict] = {}
    dropped = 0
    for model in sorted(models, key=lambda item: str(item.get("id", ""))):
        model_id = str(model.get("id", "")).strip().lower()
        if not model_id:
            continue
        if model_id in deduped:
            dropped += 1
            continue
        deduped[model_id] = model
    return list(deduped.values()), dropped


def _index_external_records(records: list[dict], registry: AliasRegistry, field_name: str) -> tuple[dict[str, dict], list[str]]:
    indexed: dict[str, dict] = {}
    unresolved: list[str] = []
    for record in records:
        name = str(record.get("model_name", "")).strip()
        if not name:
            continue
        family_id = registry.resolve(name)
        if not family_id:
            unresolved.append(name)
            continue
        current = indexed.get(family_id)
        score = float(record.get(field_name) or 0.0)
        if current is None or score > float(current.get(field_name) or 0.0):
            indexed[family_id] = record
    return indexed, sorted(set(unresolved))


def _build_entry(model: dict, *, model_id: str, provider: str, family_id: str, intelligence: int, intelligence_source: str,
                 speed_source: str, source_count: int, prompt_cost: float, completion_cost: float, average_cost: float,
                 aa_match: dict | None, lmsys_match: dict | None, top_speed: float | None, availability: int, stability: int,
                 helper_task_map: dict[str, list[str]], source_kind: str) -> dict:
    context_length = _context_length(model)
    task_metadata = infer_task_metadata(
        model_id=model_id,
        provider=provider,
        context_length=context_length,
        intelligence=intelligence,
        helper_tasks=helper_task_map.get(model_id),
    )
    source_attribution = {
        "catalog": [source_kind],
        "intelligence": intelligence_source,
        "speed": speed_source,
        "pricing": source_kind,
        "context_length": source_kind,
        "merge_priority": deepcopy(_FIELD_SOURCE_PRIORITY),
    }
    raw_metrics = {
        "intelligence_base": intelligence,
        "context_length": context_length,
        "prompt_cost": prompt_cost,
        "completion_cost": completion_cost,
        "average_cost": average_cost,
        "aa_quality": aa_match.get("quality") if aa_match else None,
        "aa_speed": aa_match.get("speed") if aa_match else None,
        "lmsys_elo": lmsys_match.get("elo") if lmsys_match else None,
        "openrouter_speed_hint": top_speed,
        "source_count": source_count,
        "availability_hint": availability,
        "stability_hint": stability,
    }
    return {
        "model": model_id,
        "family_id": family_id,
        "provider": provider,
        "display_name": model.get("name") or model_id,
        "source_attribution": source_attribution,
        "raw_metrics": raw_metrics,
        "task_metadata": task_metadata,
        "helper_metadata": {
            "helper_tasks_applied": list(helper_task_map.get(model_id, [])),
        },
        "self_hosting": derive_self_hosting_profile(
            model_id,
            source_attribution=source_attribution,
            raw_metrics=raw_metrics,
            source_kind=source_kind,
        ),
    }


def merge_catalog_sources(
    openrouter_models: list[dict],
    registry: AliasRegistry,
    aa_records: list[dict],
    lmsys_records: list[dict],
    *,
    helper_alias_map: dict[str, str] | None = None,
    helper_task_map: dict[str, list[str]] | None = None,
) -> tuple[list[dict], MergeDiagnostics]:
    helper_alias_map = helper_alias_map or {}
    helper_task_map = helper_task_map or {}
    diagnostics = MergeDiagnostics()
    catalog_models, diagnostics.deduped_catalog_duplicates = _dedupe_catalog(openrouter_models)

    for alias, family_id in helper_alias_map.items():
        registry.register(alias, family_id)

    aa_index, aa_unresolved = _index_external_records(aa_records, registry, "quality")
    lmsys_index, lmsys_unresolved = _index_external_records(lmsys_records, registry, "elo")
    diagnostics.unresolved_aliases = sorted(set(aa_unresolved + lmsys_unresolved))
    diagnostics.helper_alias_resolutions = dict(helper_alias_map)

    entries: list[dict] = []
    seen_ids: set[str] = set()

    for model in catalog_models:
        model_id = str(model.get("id", "")).strip().lower()
        if not model_id or model_id in seen_ids or "gpt-oss" in model_id:
            continue
        seen_ids.add(model_id)
        family_id = family_id_from_model_id(model_id)
        aa_match = aa_index.get(family_id)
        lmsys_match = lmsys_index.get(family_id)
        source_count = 1 + int(aa_match is not None) + int(lmsys_match is not None)
        context_length = _context_length(model)
        benchmark_value = BENCHMARK_MAP.get(model_id) or BENCHMARK_MAP.get(family_id)

        intelligence_sources: list[tuple[str, int]] = []
        if benchmark_value is not None:
            intelligence_sources.append(("benchmark", int(benchmark_value)))
        if aa_match and isinstance(aa_match.get("quality"), (int, float)):
            intelligence_sources.append(("artificial_analysis", min(100, int(float(aa_match["quality"])))) )
        if lmsys_match and isinstance(lmsys_match.get("elo"), (int, float)):
            intelligence_sources.append(("lmsys", _normalize_elo_to_intelligence(float(lmsys_match["elo"]))))

        if intelligence_sources:
            weight_map = {"benchmark": 3, "artificial_analysis": 2, "lmsys": 1}
            total_weight = sum(weight_map[source] for source, _ in intelligence_sources)
            intelligence = int(sum(weight_map[source] * value for source, value in intelligence_sources) / total_weight)
            intelligence_source = next(source for source in _FIELD_SOURCE_PRIORITY["intelligence"] if any(source == src for src, _ in intelligence_sources))
        else:
            intelligence = _heuristic_intelligence(model_id, context_length)
            intelligence_source = "heuristic"

        prompt_cost, completion_cost, average_cost = _parse_cost(model.get("pricing", {}))
        top_speed = _top_provider_speed_hint(model)
        speed_source = "artificial_analysis" if aa_match and isinstance(aa_match.get("speed"), (int, float)) else ("openrouter" if top_speed is not None else "heuristic")
        stability = _estimate_stability(model_id, model, source_count)
        availability = _estimate_availability(model_id, source_count, model)
        provider = _determine_provider(model_id)
        entry = _build_entry(
            model,
            model_id=model_id,
            provider=provider,
            family_id=family_id,
            intelligence=intelligence,
            intelligence_source=intelligence_source,
            speed_source=speed_source,
            source_count=source_count,
            prompt_cost=prompt_cost,
            completion_cost=completion_cost,
            average_cost=average_cost,
            aa_match=aa_match,
            lmsys_match=lmsys_match,
            top_speed=top_speed,
            availability=availability,
            stability=stability,
            helper_task_map=helper_task_map,
            source_kind="openrouter",
        )
        if model_id in helper_task_map:
            diagnostics.helper_task_enrichments[model_id] = list(helper_task_map[model_id])
        entries.append(entry)

    for bridge_id, info in OAUTH_BRIDGES.items():
        if bridge_id in seen_ids:
            continue
        bridge_model = {"id": bridge_id, "name": bridge_id, "context_length": 128000, "pricing": {"prompt": 0, "completion": 0}}
        intelligence = BENCHMARK_MAP.get(bridge_id, _heuristic_intelligence(bridge_id, 128000))
        entry = _build_entry(
            bridge_model,
            model_id=bridge_id,
            provider=info["provider"],
            family_id=family_id_from_model_id(bridge_id),
            intelligence=intelligence,
            intelligence_source="benchmark",
            speed_source="bridge",
            source_count=1,
            prompt_cost=0.0,
            completion_cost=0.0,
            average_cost=0.0,
            aa_match=None,
            lmsys_match=None,
            top_speed=None,
            availability=94,
            stability=92,
            helper_task_map=helper_task_map,
            source_kind="bridge",
        )
        entry.setdefault("helper_metadata", {})["bridge_note"] = info["note"]
        if bridge_id in helper_task_map:
            diagnostics.helper_task_enrichments[bridge_id] = list(helper_task_map[bridge_id])
        entries.append(entry)

    return entries, diagnostics
