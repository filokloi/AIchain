#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import sys
import re
from datetime import datetime
from copy import deepcopy
import hashlib

MANIFEST_TYPE = "aichain.catalog"
DEFAULT_BASE_URL = "https://filokloi.github.io/AIchain"

_FAST_POSITIVE_TOKENS = ("flash", "mini", "haiku", "nano", "small", "turbo", "instant", "lite", "free")
_FAST_NEGATIVE_TOKENS = ("o3-pro", "opus", "reason", "thinking", "r1", "deep-research")

def scrub_sensitive_data(obj):
    """Recursively scrub API keys and tokens from the manifest."""
    if isinstance(obj, dict):
        return {k: scrub_sensitive_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_sensitive_data(i) for i in obj]
    if isinstance(obj, str):
        # Scrub Google API keys (AIza...)
        import re
        obj = re.sub(r'AIza[0-9A-Za-z_-]{20,}', '[REDACTED_API_KEY]', obj)
        # Scrub OpenAI keys (sk-...)
        obj = re.sub(r'sk-[A-Za-z0-9]{20,}', '[REDACTED_API_KEY]', obj)
        # Scrub Bearer tokens
        obj = re.sub(r'Bearer\s+[A-Za-z0-9._-]{20,}', 'Bearer [REDACTED_TOKEN]', obj)
        return obj
    return obj

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

def build_manifest(table: dict, base_url: str = DEFAULT_BASE_URL, provider_access_runtime: dict | None = None) -> dict:
    roles = derive_roles(table)
    manifest = {
        "manifest_type": MANIFEST_TYPE,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "base_url": base_url,
        "roles": roles,
        "observability": deepcopy(provider_access_runtime or {}),
        "scoring": deepcopy(table.get("scoring", {})),
        "live_promos": deepcopy(table.get("live_promos", [])),
        "canonical_public_artifact": deepcopy(table.get("canonical_public_artifact", {})),
        "routing_hierarchy": deepcopy(table.get("routing_hierarchy", [])),
    }
    return scrub_sensitive_data(manifest)

def load_table(path: str = "config/routing_table.json") -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def write_manifest(manifest: dict, path: str = "catalog_manifest.json"):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

def main():
    table = load_table()
    manifest = build_manifest(table)
    write_manifest(manifest)
    print("Catalog manifest written successfully")

if __name__ == "__main__":
    main()
