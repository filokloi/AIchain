from __future__ import annotations

from datetime import datetime, timezone

from ..constants import ARTIFICIAL_ANALYSIS_URL
from ..snapshots import write_snapshot
from ..types import SourceResult
from .base import empty_result, fetch_json, finalize_source_health


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _extract_records(payload: object) -> list[dict]:
    models = []
    if isinstance(payload, list):
        models = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            models = payload["data"]
        elif isinstance(payload.get("models"), list):
            models = payload["models"]

    records: list[dict] = []
    for model in models:
        if not isinstance(model, dict):
            continue

        evaluations = model.get("evaluations") if isinstance(model.get("evaluations"), dict) else {}
        pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}

        name = model.get("name") or model.get("model") or model.get("slug")
        quality = _coerce_float(
            evaluations.get("artificial_analysis_intelligence_index")
            if isinstance(evaluations, dict)
            else None
        )
        if quality is None:
            quality = _coerce_float(
                model.get("Artificial Analysis Intelligence Index", model.get("quality_index", model.get("quality")))
            )

        speed = _coerce_float(model.get("median_output_tokens_per_second"))
        if speed is None:
            speed = _coerce_float(
                model.get("Output Speed", model.get("output_speed", model.get("tokens_per_second")))
            )

        if not isinstance(name, str) or not name.strip() or (quality is None and speed is None):
            continue

        records.append(
            {
                "model_name": name.strip(),
                "quality": quality,
                "speed": speed,
                "coding": _coerce_float(evaluations.get("artificial_analysis_coding_index")) if isinstance(evaluations, dict) else None,
                "math": _coerce_float(evaluations.get("artificial_analysis_math_index")) if isinstance(evaluations, dict) else None,
                "blended_price": _coerce_float(pricing.get("price_1m_blended_3_to_1")) if isinstance(pricing, dict) else None,
                "input_price": _coerce_float(pricing.get("price_1m_input_tokens")) if isinstance(pricing, dict) else None,
                "output_price": _coerce_float(pricing.get("price_1m_output_tokens")) if isinstance(pricing, dict) else None,
                "ttft_seconds": _coerce_float(model.get("median_time_to_first_token_seconds")),
                "slug": model.get("slug"),
                "creator_slug": (model.get("model_creator") or {}).get("slug") if isinstance(model.get("model_creator"), dict) else None,
                "metric_source": "artificial_analysis_v2",
            }
        )
    return records


def fetch_artificial_analysis_source(api_key: str | None = None, *, snapshot_dirs: dict | None = None) -> SourceResult:
    fetched_at = datetime.now(timezone.utc).isoformat()
    if not api_key:
        return empty_result("artificial_analysis", issue="missing ARTIFICIAL_ANALYSIS_KEY for v2 API access")
    headers = {"x-api-key": api_key, "User-Agent": "AIchain-Control-Plane/5.0"}
    payload, latency_ms, error = fetch_json(ARTIFICIAL_ANALYSIS_URL, headers=headers, timeout=20)
    if error:
        return empty_result("artificial_analysis", issue=f"fetch failed: {error}", latency_ms=latency_ms)

    records = _extract_records(payload)
    snapshot_path = write_snapshot("artificial_analysis", payload, snapshot_dirs) if snapshot_dirs else None
    health = finalize_source_health(
        "artificial_analysis",
        fetched_records=len(records),
        accepted_records=len(records),
        latency_ms=latency_ms,
        snapshot_path=snapshot_path,
    )
    if not records:
        health.warnings.append("no valid quality/speed rows parsed from Artificial Analysis v2 payload")
        health.status = "degraded"
    return SourceResult(name="artificial_analysis", records=records, health=health, raw_payload=payload, fetched_at=fetched_at)
