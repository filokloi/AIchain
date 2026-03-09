from __future__ import annotations

from datetime import datetime, timezone

from ..constants import OPENROUTER_API
from ..snapshots import write_snapshot
from ..types import SourceResult
from .base import empty_result, fetch_json, finalize_source_health


def _valid_model(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    if not record.get("id"):
        return False
    pricing = record.get("pricing", {})
    return isinstance(pricing, dict)


def fetch_openrouter_source(api_key: str | None, *, snapshot_dirs: dict | None = None) -> SourceResult:
    headers = {
        "HTTP-Referer": "https://github.com/AIchain",
        "X-Title": "AIchain Sovereign Control Plane",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload, latency_ms, error = fetch_json(OPENROUTER_API, headers=headers, timeout=30)
    fetched_at = datetime.now(timezone.utc).isoformat()
    if error:
        return empty_result("openrouter", issue=f"fetch failed: {error}", latency_ms=latency_ms)

    models = payload.get("data", []) if isinstance(payload, dict) else []
    valid = [m for m in models if _valid_model(m)]
    warnings = []
    if valid and len(valid) < len(models):
        warnings.append(f"dropped {len(models) - len(valid)} invalid catalog rows")
    snapshot_path = write_snapshot("openrouter", payload, snapshot_dirs) if snapshot_dirs else None
    health = finalize_source_health(
        "openrouter",
        fetched_records=len(models),
        accepted_records=len(valid),
        latency_ms=latency_ms,
        warnings=warnings,
        snapshot_path=snapshot_path,
    )
    return SourceResult(name="openrouter", records=valid, health=health, raw_payload=payload, fetched_at=fetched_at)
