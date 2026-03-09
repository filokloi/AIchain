from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from ..constants import OAUTH_BRIDGES, SCORING_DISPLAY_FORMULA, SCORING_VERSION, SCORING_WEIGHTS


def parse_cost(pricing: dict | None) -> float:
    if not pricing:
        return 0.0
    try:
        prompt = float(pricing.get("prompt", "0") or 0.0)
        completion = float(pricing.get("completion", "0") or 0.0)
        return (prompt + completion) / 2.0
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _normalize_log_ratio(value: float, floor: float, ceiling: float) -> float:
    if ceiling <= floor:
        return 100.0
    value = max(value, floor)
    numerator = math.log1p(value) - math.log1p(floor)
    denominator = math.log1p(ceiling) - math.log1p(floor)
    return _clamp(100.0 * numerator / denominator)


def _normalized_context(context_length: int, max_context: int) -> float:
    return _normalize_log_ratio(float(max(context_length, 4096)), 4096.0, float(max(max_context, 4096)))


def _normalized_speed(raw_speed: float | None, max_speed_hint: float) -> float:
    if raw_speed is None or raw_speed <= 0:
        return 62.0
    return _clamp(35 + 65 * (raw_speed / max(max_speed_hint, 1.0)))


def _normalized_cost_efficiency(cost: float, max_cost: float) -> float:
    if cost <= 0:
        return 100.0
    if max_cost <= 0:
        return 75.0
    scaled = math.log10(1 + (cost / max_cost) * 9)
    return _clamp(100 - (scaled * 100))


def compute_value_score(
    intelligence: int,
    speed: int,
    stability: int,
    cost: float,
    *,
    availability: float = 90.0,
    context: float = 60.0,
    task_fit: float = 60.0,
    max_cost_reference: float = 0.01,
) -> float:
    cost_efficiency = _normalized_cost_efficiency(cost, max(max_cost_reference, cost, 1e-9))
    score = (
        intelligence * SCORING_WEIGHTS["intelligence"]
        + speed * SCORING_WEIGHTS["speed"]
        + stability * SCORING_WEIGHTS["stability"]
        + cost_efficiency * SCORING_WEIGHTS["cost_efficiency"]
        + availability * SCORING_WEIGHTS["availability"]
        + context * SCORING_WEIGHTS["context"]
        + task_fit * SCORING_WEIGHTS["task_fit"]
    )
    return round(score, 2)


def classify_tier(model_id: str, cost: float) -> str:
    if model_id in OAUTH_BRIDGES:
        return "OAUTH_BRIDGE"
    if cost <= 0:
        return "FREE_FRONTIER"
    return "HEAVY_HITTER"


def tier_priority(tier: str) -> int:
    return {"OAUTH_BRIDGE": 0, "FREE_FRONTIER": 1, "HEAVY_HITTER": 2}.get(tier, 3)


def assess_geopolitical_risk(model_id: str) -> str:
    model_lower = model_id.lower()
    if any(k in model_lower for k in ("deepseek", "qwen", "zhipu", "moonshot", "yi-", "baichuan", "huawei", "internlm")):
        return "HIGH"
    if any(k in model_lower for k in ("mistral", "cohere", "upstage", "零一万物", "01-ai")):
        return "MEDIUM"
    return "LOW"


def assign_task_label(task_metadata: dict[str, Any], tier: str) -> str:
    primary = task_metadata.get("primary", [])
    if primary:
        return primary[0].upper().replace("_", "-")
    return "GENERAL-CHAT" if tier != "HEAVY_HITTER" else "REASONING"


def rank_catalog_entries(entries: list[dict], *, promo_kings: list[str] | None = None) -> tuple[list[dict], dict[str, Any]]:
    promo_kings = promo_kings or []
    max_context = max(int(entry.get("raw_metrics", {}).get("context_length") or 4096) for entry in entries) if entries else 4096
    max_cost = max(float(entry.get("raw_metrics", {}).get("average_cost") or 0.0) for entry in entries) if entries else 0.0
    max_speed_hint = max(
        float(entry.get("raw_metrics", {}).get("aa_speed") or entry.get("raw_metrics", {}).get("openrouter_speed_hint") or 0.0)
        for entry in entries
    ) if entries else 0.0
    max_speed_hint = max(max_speed_hint, 1.0)

    ranked: list[dict] = []
    for entry in entries:
        record = deepcopy(entry)
        raw = record.get("raw_metrics", {})
        intelligence = int(raw.get("intelligence_base") or 70)
        if record["model"] in promo_kings:
            intelligence = min(99, intelligence + 8)
            record.setdefault("helper_metadata", {})["promo_boost"] = True

        raw_speed = raw.get("aa_speed") if isinstance(raw.get("aa_speed"), (int, float)) else raw.get("openrouter_speed_hint")
        normalized = {
            "intelligence": round(float(intelligence), 2),
            "speed": round(_normalized_speed(float(raw_speed) if isinstance(raw_speed, (int, float)) else None, max_speed_hint), 2),
            "stability": round(float(raw.get("stability_hint") or 72), 2),
            "availability": round(float(raw.get("availability_hint") or 75), 2),
            "context": round(_normalized_context(int(raw.get("context_length") or 4096), max_context), 2),
            "task_fit": round(float(record.get("task_metadata", {}).get("coverage_score") or 60), 2),
            "cost_efficiency": round(_normalized_cost_efficiency(float(raw.get("average_cost") or 0.0), max(max_cost, float(raw.get("average_cost") or 0.0), 1e-9)), 2),
        }
        cost = float(raw.get("average_cost") or 0.0)
        final_score = compute_value_score(
            int(normalized["intelligence"]),
            int(normalized["speed"]),
            int(normalized["stability"]),
            cost,
            availability=normalized["availability"],
            context=normalized["context"],
            task_fit=normalized["task_fit"],
            max_cost_reference=max(max_cost, cost, 1e-9),
        )
        tier = classify_tier(record["model"], cost)
        record["normalized_metrics"] = normalized
        record["metrics"] = {
            "intelligence": int(round(normalized["intelligence"])),
            "speed": int(round(normalized["speed"])),
            "stability": int(round(normalized["stability"])),
            "availability": int(round(normalized["availability"])),
            "context": int(round(normalized["context"])),
            "task_fit": int(round(normalized["task_fit"])),
            "cost_efficiency": int(round(normalized["cost_efficiency"])),
            "cost": round(cost, 8),
        }
        record["tier"] = tier
        record["task_label"] = assign_task_label(record.get("task_metadata", {}), tier)
        record["geopolitical_risk"] = assess_geopolitical_risk(record["model"])
        record["score_breakdown"] = {key: round(normalized[key] * weight, 2) for key, weight in SCORING_WEIGHTS.items()}
        record["value_score"] = final_score
        if record["model"] in OAUTH_BRIDGES:
            record["bridge_note"] = OAUTH_BRIDGES[record["model"]]["note"]
        ranked.append(record)

    ranked.sort(key=lambda item: (tier_priority(item["tier"]), -item["value_score"], -item["metrics"]["intelligence"], item["model"]))
    for idx, entry in enumerate(ranked, start=1):
        entry["rank"] = idx

    scoring = {
        "version": SCORING_VERSION,
        "display_formula": SCORING_DISPLAY_FORMULA,
        "weights": deepcopy(SCORING_WEIGHTS),
        "notes": {
            "raw_metrics": "Provider, catalog, and benchmark values before normalization.",
            "normalized_metrics": "Metrics normalized to a 0-100 scale for cross-model comparison.",
            "final_score": "Weighted mission score combining quality, stability, speed, availability, context, task coverage, and price-performance.",
        },
    }
    return ranked, scoring
