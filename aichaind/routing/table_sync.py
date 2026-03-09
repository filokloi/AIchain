#!/usr/bin/env python3
"""
aichaind.routing.table_sync — Routing Table Fetch & Validation

Migrated from ai-chain-skill/scripts/sync.py.
Handles fetching, validating, caching, and recalculating the routing table.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from aichaind.routing.catalog_contract import validate_catalog_manifest

try:
    import requests
except ImportError:
    requests = None

MAX_RETRIES = 3
BASE_BACKOFF = 2


def fetch_routing_table(url: str, log: logging.Logger, version_compat: dict = None) -> dict | None:
    """Fetch, validate, and return routing table or None."""
    if not requests:
        log.error("'requests' not installed")
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"Fetch routing table (attempt {attempt}/{MAX_RETRIES})")
            resp = requests.get(url, timeout=30, headers={
                "User-Agent": "aichaind/5.0",
                "Accept": "application/json",
            })
            resp.raise_for_status()
            data = resp.json()

            contract = validate_catalog_manifest(data, version_compat)
            if not contract.valid:
                for issue in contract.issues:
                    log.error(f"Catalog contract invalid: {issue}")
                return None
            for warning in contract.warnings:
                log.warning(f"Catalog contract warning: {warning}")
            data["_aichaind_contract"] = contract.to_metadata()

            synopsis = data.get("last_synopsis")
            if synopsis:
                try:
                    dt = datetime.fromisoformat(synopsis.replace("Z", "+00:00"))
                    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    log.info(f"Table age: {age_h:.1f}h")
                except ValueError:
                    pass

            log.info(
                f"Routing table OK: {data.get('total_models_analyzed', '?')} models, "
                f"schema={contract.schema_version} mode={contract.compat_mode}"
            )
            return data

        except Exception as exc:
            wait = BASE_BACKOFF ** attempt
            log.warning(f"Fetch failed: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    log.error("All fetch attempts exhausted")
    return None


def get_best_free_primary(table: dict) -> dict | None:
    """Find best $0/effective-$0 model."""
    candidates = []
    for entry in table.get("routing_hierarchy", []):
        if entry.get("access_type") == "manual_assist":
            continue
        cost = entry.get("metrics", {}).get("effective_cost",
               entry.get("metrics", {}).get("cost", 1))
        if entry.get("tier") in ("OAUTH_BRIDGE", "FREE_FRONTIER") or cost <= 0.00000001:
            candidates.append(entry)

    if not candidates:
        return None

    openrouter_candidates = [c for c in candidates if c.get("model", "").startswith("openrouter/")]
    if openrouter_candidates:
        return openrouter_candidates[0]
    return candidates[0]


def get_heavy_hitter(table: dict) -> dict | None:
    """Get the designated rescue model."""
    hh = table.get("heavy_hitter", {})
    model_id = hh.get("model")
    if model_id and model_id != "N/A":
        for entry in table.get("routing_hierarchy", []):
            if entry["model"] == model_id:
                return entry
    hierarchy = table.get("routing_hierarchy", [])
    if hierarchy:
        return max(hierarchy, key=lambda e: e.get("metrics", {}).get("intelligence", 0))
    return None


def get_top_fallbacks(table: dict, exclude_model: str, max_count: int = 5) -> list[str]:
    """Get top-ranked fallback model IDs excluding the primary."""
    return [e["model"] for e in table.get("routing_hierarchy", [])
            if e["model"] != exclude_model][:max_count]


def compute_table_checksum(table: dict) -> str:
    """SHA256 fingerprint of the routing hierarchy for change detection."""
    hierarchy = table.get("routing_hierarchy", [])
    raw = json.dumps([(e["model"], e.get("value_score", 0)) for e in hierarchy],
                     sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _ver_gte(a: str, b: str) -> bool:
    """Check if version a >= version b."""
    def parse(v):
        nums = []
        for part in v.split("-")[0].split("."):
            try:
                nums.append(int(part))
            except ValueError:
                nums.append(0)
        return nums
    return parse(a) >= parse(b)
