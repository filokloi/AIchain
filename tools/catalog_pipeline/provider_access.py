from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from tools.catalog_pipeline.constants import PROVIDER_ACCESS_MATRIX

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_SNAPSHOT = REPO_ROOT / "artifacts" / "provider_access_runtime.json"


def load_provider_access_runtime_snapshot(path: Path | None = None) -> dict[str, Any]:
    snapshot_path = Path(path) if path else DEFAULT_RUNTIME_SNAPSHOT
    if not snapshot_path.exists():
        return {}
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def build_provider_access_matrix(runtime_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    matrix = deepcopy(PROVIDER_ACCESS_MATRIX)
    snapshot = runtime_snapshot if runtime_snapshot is not None else load_provider_access_runtime_snapshot()
    providers = snapshot.get("providers", snapshot) if isinstance(snapshot, dict) else {}
    if isinstance(providers, dict):
        for provider, override in providers.items():
            if not isinstance(override, dict):
                continue
            base = matrix.get(provider, {})
            matrix[provider] = _deep_merge(base, override)

    for provider, info in matrix.items():
        if not isinstance(info, dict):
            continue
        info.setdefault("provider_id", provider)
        info.setdefault("factual_state", info.get("overall_mode", "unknown"))
        info.setdefault("fallback_path", [])
        methods = info.setdefault("methods", {})
        if not isinstance(methods, dict):
            methods = {}
            info["methods"] = methods
        official_methods: list[str] = []
        runtime_methods: list[str] = []
        provider_last_verified = info.get("last_verified_at", "")
        for method_name, method_info in methods.items():
            if not isinstance(method_info, dict):
                continue
            method_info.setdefault("mode", info.get("overall_mode", "unknown"))
            method_info.setdefault("official_support", False)
            method_info.setdefault("runtime_confirmed", method_info.get("mode") == "runtime_confirmed")
            method_info.setdefault("quota_visibility", info.get("quota_visibility", ""))
            method_info.setdefault("limit_type", info.get("limit_type", ""))
            method_info.setdefault("fallback_path", list(info.get("fallback_path", [])))
            method_info.setdefault("last_verified_at", "")
            method_info.setdefault("verification_basis", "")
            if method_info.get("official_support"):
                official_methods.append(method_name)
            if method_info.get("runtime_confirmed") or method_info.get("mode") == "runtime_confirmed":
                runtime_methods.append(method_name)
            if not provider_last_verified and method_info.get("last_verified_at"):
                provider_last_verified = method_info.get("last_verified_at")
        info.setdefault("officially_supported_methods", official_methods)
        info.setdefault("runtime_confirmed_methods", runtime_methods)
        if provider_last_verified:
            info["last_verified_at"] = provider_last_verified
    return matrix


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
