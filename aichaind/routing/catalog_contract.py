#!/usr/bin/env python3
"""
aichaind.routing.catalog_contract — Global Catalog / Local Execution Contract

Defines the compatibility boundary between:
    - the global AIchain site/feed plane
    - the local OpenClaw skill + aichaind execution plane

The current production feed is still v4-shaped, so validation supports:
    - legacy_v4 compatibility mode
    - native_v5 manifest mode
"""

from __future__ import annotations

from dataclasses import dataclass, field


SUPPORTED_MANIFEST_TYPE = "aichain.catalog"
MIN_SUPPORTED_SCHEMA_MAJOR = 4
MAX_SUPPORTED_SCHEMA_MAJOR = 5

_REQUIRED_ENTRY_FIELDS = frozenset({"model", "tier", "metrics"})
_REQUIRED_METRICS = frozenset({"intelligence", "speed", "stability", "cost"})
_ROLE_KEYS = ("fast", "heavy", "visual")
_FAST_POSITIVE_TOKENS = (
    "flash", "mini", "haiku", "nano", "small", "turbo", "instant", "lite", "free",
)
_FAST_NEGATIVE_TOKENS = (
    "o3-pro", "opus", "reason", "thinking", "r1", "deep-research",
)


@dataclass
class ContractValidation:
    """Result of validating a site/feed contract."""

    valid: bool = False
    schema_version: str = ""
    compat_mode: str = ""
    manifest_type: str = ""
    roles: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict:
        return {
            "valid": self.valid,
            "schema_version": self.schema_version,
            "compat_mode": self.compat_mode,
            "manifest_type": self.manifest_type,
            "roles": dict(self.roles),
            "issues": list(self.issues),
            "warnings": list(self.warnings),
        }


def validate_catalog_manifest(data: dict, version_compat: dict | None = None) -> ContractValidation:
    """
    Validate a routing table / catalog manifest.

    Accepts the current v4 feed in compatibility mode and a future v5 catalog
    manifest in native mode.
    """
    result = ContractValidation()
    if not isinstance(data, dict):
        result.issues.append("manifest must be a JSON object")
        return result

    result.schema_version = str(data.get("schema_version") or data.get("version") or "")
    result.manifest_type = str(data.get("manifest_type") or "")
    major = _parse_major(result.schema_version)
    if major is None:
        result.issues.append("missing schema/version field")
        return result

    if major < MIN_SUPPORTED_SCHEMA_MAJOR:
        result.issues.append(
            f"unsupported schema version {result.schema_version} (< {MIN_SUPPORTED_SCHEMA_MAJOR}.0)"
        )
    if major > MAX_SUPPORTED_SCHEMA_MAJOR:
        result.issues.append(
            f"unsupported future schema version {result.schema_version} (> {MAX_SUPPORTED_SCHEMA_MAJOR}.x)"
        )

    if version_compat:
        min_ver = version_compat.get("min_routing_table_version")
        if min_ver and not _ver_gte(result.schema_version, min_ver):
            result.issues.append(
                f"manifest version {result.schema_version} is below required {min_ver}"
            )

    hierarchy = data.get("routing_hierarchy")
    if not isinstance(hierarchy, list) or not hierarchy:
        result.issues.append("missing or empty routing_hierarchy")
        return result

    for idx, entry in enumerate(hierarchy, start=1):
        _validate_entry(entry, idx, result.issues, result.warnings)

    if major >= 5:
        result.compat_mode = "native_v5"
        _validate_v5_topology(data, result.issues, result.warnings)
        result.roles = _extract_v5_roles(data, result.warnings)
    else:
        result.compat_mode = "legacy_v4"
        if "schema_version" not in data:
            result.warnings.append("legacy feed missing schema_version; using version compatibility mode")
        result.roles = _derive_legacy_roles(data)

    if not result.roles.get("fast"):
        result.warnings.append("catalog does not define a fast role")
    if not result.roles.get("heavy"):
        result.warnings.append("catalog does not define a heavy role")
    if not result.roles.get("visual"):
        result.warnings.append("catalog does not define a visual role")

    hierarchy_models = {entry.get("model", "") for entry in hierarchy if isinstance(entry, dict)}
    for role_name, model_id in result.roles.items():
        if model_id and model_id not in hierarchy_models:
            result.warnings.append(
                f"{role_name} role model {model_id!r} is not present in routing_hierarchy"
            )

    result.valid = not result.issues
    return result


def _validate_v5_topology(data: dict, issues: list[str], warnings: list[str]) -> None:
    if data.get("manifest_type") != SUPPORTED_MANIFEST_TYPE:
        issues.append(f"native_v5 manifest_type must be {SUPPORTED_MANIFEST_TYPE!r}")

    planes = data.get("planes")
    if not isinstance(planes, dict):
        issues.append("native_v5 manifest missing planes object")
        return

    global_plane = planes.get("global")
    local_plane = planes.get("local")
    if not isinstance(global_plane, dict):
        issues.append("native_v5 manifest missing planes.global")
    elif global_plane.get("kind") != "catalog":
        issues.append("planes.global.kind must be 'catalog'")

    if not isinstance(local_plane, dict):
        issues.append("native_v5 manifest missing planes.local")
    elif local_plane.get("kind") != "execution":
        issues.append("planes.local.kind must be 'execution'")

    if "capabilities" not in data:
        warnings.append("native_v5 manifest missing capabilities block")


def _validate_entry(entry: dict, index: int, issues: list[str], warnings: list[str]) -> None:
    if not isinstance(entry, dict):
        issues.append(f"routing_hierarchy[{index}] is not an object")
        return

    missing = sorted(_REQUIRED_ENTRY_FIELDS - set(entry.keys()))
    if missing:
        issues.append(f"routing_hierarchy[{index}] missing fields: {', '.join(missing)}")
        return

    metrics = entry.get("metrics")
    if not isinstance(metrics, dict):
        issues.append(f"routing_hierarchy[{index}].metrics must be an object")
        return

    metric_missing = sorted(_REQUIRED_METRICS - set(metrics.keys()))
    if metric_missing:
        issues.append(
            f"routing_hierarchy[{index}].metrics missing: {', '.join(metric_missing)}"
        )

    provider = entry.get("provider")
    if provider is None:
        warnings.append(f"routing_hierarchy[{index}] missing provider field")


def _extract_v5_roles(data: dict, warnings: list[str]) -> dict[str, str]:
    roles_block = data.get("roles")
    if not isinstance(roles_block, dict):
        warnings.append("native_v5 manifest missing roles block")
        return {}

    roles: dict[str, str] = {}
    for role_name in _ROLE_KEYS:
        raw = roles_block.get(role_name)
        if isinstance(raw, dict):
            roles[role_name] = str(raw.get("model") or "")
        elif isinstance(raw, str):
            roles[role_name] = raw
        else:
            roles[role_name] = ""
    return roles


def _derive_legacy_roles(data: dict) -> dict[str, str]:
    hierarchy = data.get("routing_hierarchy", [])
    roles = {"fast": "", "heavy": "", "visual": ""}

    fast_candidates = []
    for entry in hierarchy:
        metrics = entry.get("metrics", {})
        cost = metrics.get("effective_cost", metrics.get("cost", 1))
        if entry.get("tier") in ("OAUTH_BRIDGE", "FREE_FRONTIER") or cost <= 0.00001:
            fast_candidates.append(entry)

    if fast_candidates:
        roles["fast"] = max(fast_candidates, key=_fast_score).get("model", "")

    heavy_hitter = data.get("heavy_hitter", {})
    if isinstance(heavy_hitter, dict) and heavy_hitter.get("model"):
        roles["heavy"] = heavy_hitter["model"]
    elif hierarchy:
        roles["heavy"] = max(
            hierarchy,
            key=lambda item: item.get("metrics", {}).get("intelligence", 0),
        ).get("model", "")

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


def _parse_major(version: str) -> int | None:
    if not version:
        return None
    head = version.split("-", 1)[0]
    part = head.split(".", 1)[0]
    try:
        return int(part)
    except ValueError:
        return None


def _ver_gte(a: str, b: str) -> bool:
    def parse(v: str) -> list[int]:
        values: list[int] = []
        for part in v.split("-", 1)[0].split("."):
            try:
                values.append(int(part))
            except ValueError:
                values.append(0)
        return values

    return parse(a) >= parse(b)
