#!/usr/bin/env python3
"""Build a native v5 catalog manifest from the legacy AIchain routing table."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aichaind.routing.catalog_contract import validate_catalog_manifest
from aichaind.routing.table_sync import compute_table_checksum

TABLE_FILE = REPO_ROOT / "ai_routing_table.json"
MANIFEST_FILE = REPO_ROOT / "catalog_manifest.json"
DEFAULT_BASE_URL = "https://filokloi.github.io/AIchain"
SCHEMA_VERSION = "5.0.0"
MANIFEST_TYPE = "aichain.catalog"
_FAST_POSITIVE_TOKENS = ("flash", "mini", "haiku", "nano", "small", "turbo", "instant", "lite", "free")
_FAST_NEGATIVE_TOKENS = ("o3-pro", "opus", "reason", "thinking", "r1", "deep-research")


def derive_roles(table: dict) -> dict[str, str]:
    hierarchy = table.get("routing_hierarchy", [])
    roles = {"fast": "", "heavy": "", "visual": ""}

    fast_candidates = []
    for entry in hierarchy:
        metrics = entry.get("metrics", {})
        cost = metrics.get("effective_cost", metrics.get("cost", 1))
        if entry.get("tier") in ("OAUTH_BRIDGE", "FREE_FRONTIER") or cost <= 0.00001:
            fast_candidates.append(entry)
    if fast_candidates:
        roles["fast"] = max(fast_candidates, key=_fast_score).get("model", "")

    heavy_hitter = table.get("heavy_hitter", {})
    if isinstance(heavy_hitter, dict) and heavy_hitter.get("model"):
        roles["heavy"] = heavy_hitter["model"]
    elif hierarchy:
        roles["heavy"] = max(hierarchy, key=lambda item: item.get("metrics", {}).get("intelligence", 0)).get("model", "")

    visual_candidates = []
    for entry in hierarchy:
        model_id = str(entry.get("model", "")).lower()
        if any(token in model_id for token in ("gpt-4o", "vision", "gemini", "-vl", "/vl")):
            visual_candidates.append(entry)
    if visual_candidates:
        roles["visual"] = max(visual_candidates, key=_visual_score).get("model", "")

    if not roles["visual"]:
        roles["visual"] = roles["fast"] or roles["heavy"]
    return roles


def _fast_score(entry: dict) -> float:
    model_id = str(entry.get("model", "")).lower()
    metrics = entry.get("metrics", {})
    score = metrics.get("speed", 0) * 4 + metrics.get("stability", 0) * 2 + metrics.get("intelligence", 0)
    if entry.get("tier") == "FREE_FRONTIER":
        score += 25
    elif entry.get("tier") == "OAUTH_BRIDGE":
        score += 15
    cost = metrics.get("effective_cost", metrics.get("cost", 1))
    if cost <= 0:
        score += 20
    elif cost <= 0.00001:
        score += 8
    for token in _FAST_POSITIVE_TOKENS:
        if token in model_id:
            score += 18
    for token in _FAST_NEGATIVE_TOKENS:
        if token in model_id:
            score -= 30
    return score


def _visual_score(entry: dict) -> float:
    model_id = str(entry.get("model", "")).lower()
    metrics = entry.get("metrics", {})
    score = metrics.get("intelligence", 0) * 3 + metrics.get("stability", 0) * 2 + metrics.get("speed", 0)
    if "gpt-4o" in model_id:
        score += 25
    if any(token in model_id for token in ("vision", "-vl", "/vl")):
        score += 20
    if "gemini" in model_id:
        score += 10
    return score


def build_manifest(table: dict, base_url: str = DEFAULT_BASE_URL) -> dict:
    roles = derive_roles(table)
    checksum = compute_table_checksum(table)
    generated_at = datetime.now(timezone.utc).isoformat()
    manifest_url = f"{base_url}/catalog_manifest.json"
    legacy_url = f"{base_url}/ai_routing_table.json"

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": MANIFEST_TYPE,
        "system_status": table.get("system_status", "UNKNOWN"),
        "last_synopsis": table.get("last_synopsis", generated_at),
        "generated_at": generated_at,
        "planes": {
            "global": {
                "kind": "catalog",
                "site_url": f"{base_url}/",
                "manifest_url": manifest_url,
                "legacy_feed_url": legacy_url,
            },
            "local": {
                "kind": "execution",
                "skill": "openclaw",
                "sidecar": "aichaind",
                "install_mode": "private_workspace",
            },
        },
        "roles": {
            "fast": {"model": roles["fast"], "source": "derived_fast_role"},
            "heavy": {"model": roles["heavy"], "source": "heavy_hitter_or_max_intelligence"},
            "visual": {"model": roles["visual"], "source": "derived_visual_capability"},
        },
        "capabilities": {
            "supports_a2a": False,
            "supports_loss_aware_compression": False,
            "supports_local_canonical_state": True,
            "supports_policy_gated_privacy": True,
            "supports_cost_optimization": True,
        },
        "catalog": {
            "scope": table.get("scope", "GLOBAL_NON_DISCRIMINATORY"),
            "philosophy": table.get("philosophy", "Maximum Intelligence at Zero Cost."),
            "legacy_feed_version": table.get("version", ""),
            "feed_checksum": checksum,
            "total_models_analyzed": table.get("total_models_analyzed", 0),
            "data_sources": deepcopy(table.get("data_sources", {})),
            "tier_breakdown": deepcopy(table.get("tier_breakdown", {})),
            "heavy_hitter": deepcopy(table.get("heavy_hitter", {})),
        },
        "observability": {
            "source_health": deepcopy(table.get("source_health", {})),
            "degradation_reasons": deepcopy(table.get("degradation_reasons", [])),
            "helper_ai": deepcopy(table.get("helper_ai", {})),
            "merge_diagnostics": deepcopy(table.get("merge_diagnostics", {})),
        },
        "operational_status": deepcopy(table.get("operational_status", {})),
        "public_artifact_readiness": deepcopy(table.get("public_artifact_readiness", {})),
        "scoring": deepcopy(table.get("scoring", {})),
        "live_promos": deepcopy(table.get("live_promos", [])),
        "canonical_public_artifact": deepcopy(table.get("canonical_public_artifact", {})),
        "routing_hierarchy": deepcopy(table.get("routing_hierarchy", [])),
    }

    validation = validate_catalog_manifest(manifest)
    if not validation.valid:
        joined = "; ".join(validation.issues)
        raise ValueError(f"generated catalog manifest is invalid: {joined}")
    return manifest


def load_table(path: Path = TABLE_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(manifest: dict, path: Path = MANIFEST_FILE) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    table = load_table()
    manifest = build_manifest(table)
    write_manifest(manifest)
    print(f"[AIchain] Catalog manifest written to {MANIFEST_FILE}")


if __name__ == "__main__":
    main()



