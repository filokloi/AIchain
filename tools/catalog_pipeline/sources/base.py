from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from ..constants import SOURCE_THRESHOLDS
from ..types import SourceHealth, SourceResult


def fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> tuple[Any, int, str | None]:
    started = time.perf_counter()
    try:
        response = requests.get(url, headers=headers or {}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return data, latency_ms, None
    except Exception as exc:  # pragma: no cover
        latency_ms = int((time.perf_counter() - started) * 1000)
        return None, latency_ms, str(exc)


def fetch_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> tuple[str | None, int, str | None]:
    started = time.perf_counter()
    try:
        response = requests.get(url, headers=headers or {}, timeout=timeout)
        response.raise_for_status()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return response.text, latency_ms, None
    except Exception as exc:  # pragma: no cover
        latency_ms = int((time.perf_counter() - started) * 1000)
        return None, latency_ms, str(exc)


def fetch_bytes(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> tuple[bytes | None, int, str | None]:
    started = time.perf_counter()
    try:
        response = requests.get(url, headers=headers or {}, timeout=timeout)
        response.raise_for_status()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return response.content, latency_ms, None
    except Exception as exc:  # pragma: no cover
        latency_ms = int((time.perf_counter() - started) * 1000)
        return None, latency_ms, str(exc)


def finalize_source_health(
    name: str,
    *,
    fetched_records: int,
    accepted_records: int,
    latency_ms: int | None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    snapshot_path: str | None = None,
) -> SourceHealth:
    issues = issues or []
    warnings = warnings or []
    coverage = (accepted_records / fetched_records) if fetched_records else 0.0
    thresholds = SOURCE_THRESHOLDS.get(name, {"min_records": 1, "min_coverage": 0.0})
    healthy = accepted_records >= thresholds["min_records"] and coverage >= thresholds["min_coverage"]
    if healthy:
        status = "ok"
    elif accepted_records > 0:
        status = "degraded"
    else:
        status = "failed"
    return SourceHealth(
        name=name,
        status=status,
        healthy=healthy,
        fetched_records=fetched_records,
        accepted_records=accepted_records,
        coverage=coverage,
        latency_ms=latency_ms,
        issues=issues,
        warnings=warnings,
        snapshot_path=snapshot_path,
    )


def empty_result(name: str, *, issue: str, latency_ms: int | None = None) -> SourceResult:
    health = finalize_source_health(
        name,
        fetched_records=0,
        accepted_records=0,
        latency_ms=latency_ms,
        issues=[issue],
    )
    return SourceResult(name=name, records=[], health=health, raw_payload=None, fetched_at=datetime.now(timezone.utc).isoformat())
