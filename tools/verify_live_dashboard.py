#!/usr/bin/env python3
"""Verify the live GitHub Pages dashboard/control-plane state."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict

import requests

LIVE_SITE_URL = "https://filokloi.github.io/AIchain/"
LIVE_MANIFEST_URL = "https://filokloi.github.io/AIchain/catalog_manifest.json"
LIVE_LEGACY_URL = "https://filokloi.github.io/AIchain/ai_routing_table.json"


@dataclass
class LiveDashboardStatus:
    status: str
    site_http_ok: bool
    manifest_http_ok: bool
    site_uses_canonical: bool
    site_uses_legacy: bool
    manifest_valid: bool
    safe_to_switch_claim_present: bool
    reasons: list[str]


def fetch_text(url: str) -> tuple[bool, str, int]:
    try:
        response = requests.get(url, timeout=20)
        return response.status_code == 200, response.text, response.status_code
    except Exception as exc:
        return False, str(exc), 0


def classify_live_dashboard_status(index_html: str, manifest_text: str, site_http_ok: bool, manifest_http_ok: bool) -> LiveDashboardStatus:
    site_uses_canonical = "catalog_manifest.json" in index_html
    site_uses_legacy = "ai_routing_table.json" in index_html
    manifest_valid = "aichain.catalog" in manifest_text and "dashboard_switch_ready" in manifest_text
    safe_to_switch_claim_present = "safe_to_switch_dashboard_to_canonical_artifact" in manifest_text

    reasons: list[str] = []
    if not site_http_ok:
        reasons.append("live dashboard HTML not reachable")
    if not site_uses_canonical:
        reasons.append("live dashboard still does not reference catalog_manifest.json")
    if not manifest_http_ok:
        reasons.append("live catalog_manifest.json not reachable")
    if manifest_http_ok and not manifest_valid:
        reasons.append("live catalog_manifest.json missing canonical readiness markers")

    if site_http_ok and manifest_http_ok and site_uses_canonical and manifest_valid:
        if site_uses_legacy:
            status = "deploy_confirmed_with_rollback"
            reasons.append("legacy ai_routing_table.json rollback path still active")
        else:
            status = "deploy_confirmed"
    elif site_http_ok and not manifest_http_ok:
        status = "deploy_not_switched"
        if site_uses_legacy:
            reasons.append("live dashboard still references legacy ai_routing_table.json path")
    else:
        status = "deploy_not_confirmed"
        if site_uses_legacy:
            reasons.append("live dashboard still references legacy ai_routing_table.json path")

    return LiveDashboardStatus(
        status=status,
        site_http_ok=site_http_ok,
        manifest_http_ok=manifest_http_ok,
        site_uses_canonical=site_uses_canonical,
        site_uses_legacy=site_uses_legacy,
        manifest_valid=manifest_valid,
        safe_to_switch_claim_present=safe_to_switch_claim_present,
        reasons=reasons,
    )


def main() -> int:
    site_ok, site_text, site_code = fetch_text(LIVE_SITE_URL)
    manifest_ok, manifest_text, manifest_code = fetch_text(LIVE_MANIFEST_URL)
    legacy_ok, _, legacy_code = fetch_text(LIVE_LEGACY_URL)

    result = classify_live_dashboard_status(
        index_html=site_text if site_ok else "",
        manifest_text=manifest_text if manifest_ok else "",
        site_http_ok=site_ok,
        manifest_http_ok=manifest_ok,
    )

    payload = asdict(result)
    payload["site_status_code"] = site_code
    payload["manifest_status_code"] = manifest_code
    payload["legacy_status_code"] = legacy_code
    payload["legacy_reachable"] = legacy_ok
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    return 0 if result.status in {"deploy_confirmed", "deploy_confirmed_with_rollback"} else 1


if __name__ == "__main__":
    sys.exit(main())
