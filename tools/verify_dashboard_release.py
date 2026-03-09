#!/usr/bin/env python3
"""Verify that the public dashboard cutover contract remains safe to deploy."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "index.html"
MANIFEST_PATH = REPO_ROOT / "catalog_manifest.json"


class VerificationError(RuntimeError):
    pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def verify_dashboard_source(index_html: str) -> None:
    required = [
        "const CANONICAL_SOURCE_URL = 'catalog_manifest.json';",
        "const LEGACY_SOURCE_URL = 'ai_routing_table.json';",
        "normalizeRoutingPayload(payload, 'canonical', CANONICAL_SOURCE_URL)",
        "normalizeRoutingPayload(payload, 'legacy_rollback', LEGACY_SOURCE_URL)",
        "Canonical manifest unavailable, using legacy rollback feed",
        "canonical v5 catalog manifest is now the primary public artifact",
    ]
    missing = [snippet for snippet in required if snippet not in index_html]
    if missing:
        raise VerificationError(f"dashboard cutover verification failed; missing snippets: {missing}")


def verify_manifest(manifest: dict) -> None:
    if manifest.get("manifest_type") != "aichain.catalog":
        raise VerificationError("catalog_manifest.json is not a canonical AIchain catalog manifest")

    readiness = manifest.get("public_artifact_readiness", {})
    if readiness.get("dashboard_switch_ready") is not True:
        raise VerificationError(
            "canonical manifest is not marked dashboard_switch_ready=true; "
            f"recommended_state={readiness.get('recommended_state', 'unknown')}"
        )

    migration_state = (manifest.get("canonical_public_artifact") or {}).get("migration_state", "")
    if migration_state != "safe_to_switch_dashboard_to_canonical_artifact":
        raise VerificationError(
            "canonical artifact migration_state does not permit dashboard cutover; "
            f"got={migration_state or 'missing'}"
        )


def main() -> None:
    verify_dashboard_source(_read(INDEX_HTML))
    verify_manifest(json.loads(_read(MANIFEST_PATH)))
    print("[AIchain] Dashboard cutover release contract verified")


if __name__ == "__main__":
    main()
