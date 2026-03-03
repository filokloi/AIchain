#!/usr/bin/env python3
"""
AIchain Sync — Routing table fetch + integrity + freshness validation.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

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
                "User-Agent": "AIchain-Skill/4.0",
                "Accept": "application/json",
            })
            resp.raise_for_status()
            data = resp.json()

            # Structure validation
            if "routing_hierarchy" not in data:
                log.error("Invalid table: missing 'routing_hierarchy'")
                return None
            if not isinstance(data["routing_hierarchy"], list) or len(data["routing_hierarchy"]) == 0:
                log.error("Invalid table: empty hierarchy")
                return None

            # Version compatibility
            if version_compat:
                table_ver = data.get("version", "0.0")
                min_ver = version_compat.get("min_routing_table_version", "0.0")
                if not _ver_gte(table_ver, min_ver):
                    log.error(f"Version mismatch: table={table_ver}, required>={min_ver}")
                    return None

            # Freshness
            synopsis = data.get("last_synopsis")
            if synopsis:
                try:
                    dt = datetime.fromisoformat(synopsis.replace("Z", "+00:00"))
                    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    log.info(f"Table age: {age_h:.1f}h")
                except ValueError:
                    pass

            log.info(f"Routing table OK: {data.get('total_models_analyzed', '?')} models, "
                     f"v{data.get('version', '?')}")
            return data

        except Exception as exc:
            wait = BASE_BACKOFF ** attempt
            log.warning(f"Fetch failed: {exc}")
            if attempt < MAX_RETRIES:
                import time
                time.sleep(wait)

    log.error("All fetch attempts exhausted")
    return None


def apply_scenario_recalculation(table: dict, scenario: dict, log: logging.Logger) -> dict:
    """Recalculate Value Scores locally based on user scenario overrides."""
    if not table or "routing_hierarchy" not in table or not scenario:
        return table

    log.info(f"Applying user scenario: {scenario.get('scenario_id', 'unknown')}")
    overrides = scenario.get("model_overrides", {})
    login_only = scenario.get("login_only_models", {})

    COST_EPSILON = 0.00000001
    
    new_hierarchy = []
    for entry in table["routing_hierarchy"]:
        model_id = entry["model"]
        metrics = entry.get("metrics", {})
        intelligence = metrics.get("intelligence", 0)
        
        # Determine effective cost
        effective_cost = metrics.get("cost", 0)
        override = overrides.get(model_id)
        if override:
            if "effective_cost" in override:
                effective_cost = override["effective_cost"]
            if "tier" in override:
                entry["tier"] = override["tier"]
                
        # Determine priority override
        priority = 100.0 if override else 1.0  # Boost explicitly overridden models
        login_access = login_only.get(model_id)
        if login_access:
            priority = login_access.get("priority_override", 1.0)
            entry["access_type"] = login_access.get("access", "standard")
            
        # Recalculate Value Score
        # (Intelligence * Priority) / (Effective_Cost + EPSILON)
        val_score = (intelligence * priority) / (effective_cost + COST_EPSILON)
        entry["value_score"] = float(val_score)
        entry["metrics"]["effective_cost"] = effective_cost
        
        new_hierarchy.append(entry)
        
    # Sort descending by derived value score
    new_hierarchy.sort(key=lambda x: x["value_score"], reverse=True)
    table["routing_hierarchy"] = new_hierarchy
    
    # Save the local calculation for transparency
    local_path = Path("~/.openclaw/aichain/recalculated_ranking.json").expanduser()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(table, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to save local recalculation: {e}")
        
    return table


def get_best_free_primary(table: dict) -> dict | None:
    """Find best $0/effective-$0 model, explicitly avoiding manual-assist-only models.
    Prioritizes OpenRouter models if multiple free options are available."""
    candidates = []
    for entry in table.get("routing_hierarchy", []):
        if entry.get("access_type") == "manual_assist":
            continue
        # We consider FREE_FRONTIER, OAUTH_BRIDGE, or anyone whose effective_cost is 0.0
        cost = entry.get("metrics", {}).get("effective_cost", entry.get("metrics", {}).get("cost", 1))
        if entry.get("tier") in ("OAUTH_BRIDGE", "FREE_FRONTIER") or cost <= 0.00000001:
            candidates.append(entry)
    
    if not candidates:
        return None
        
    # Mission Alignment: If we have multiple candidates, prioritize OpenRouter
    openrouter_candidates = [c for c in candidates if c.get("model", "").startswith("openrouter/")]
    if openrouter_candidates:
        return openrouter_candidates[0] # Top of hierarchy among OpenRouter models
        
    return candidates[0]


def get_heavy_hitter(table: dict) -> dict | None:
    """Get the designated rescue model."""
    hh = table.get("heavy_hitter", {})
    model_id = hh.get("model")
    if model_id and model_id != "N/A":
        for entry in table.get("routing_hierarchy", []):
            if entry["model"] == model_id:
                return entry
    # Fallback: highest intelligence
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
    """Check if version a >= version b (e.g., '4.0-sovereign' >= '4.0')."""
    def parse(v):
        nums = []
        for part in v.split("-")[0].split("."):
            try:
                nums.append(int(part))
            except ValueError:
                nums.append(0)
        return nums
    return parse(a) >= parse(b)
