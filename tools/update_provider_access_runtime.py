#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aichaind.providers.adapters.openai_codex import OpenAICodexOAuthAdapter

SNAPSHOT_PATH = REPO_ROOT / "artifacts" / "provider_access_runtime.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fetch_health() -> dict:
    try:
        with urlopen("http://127.0.0.1:8080/health", timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return {}


def _build_snapshot() -> dict:
    payload = _load_json(SNAPSHOT_PATH)
    if not isinstance(payload, dict):
        payload = {}
    providers = payload.get("providers", {}) if isinstance(payload.get("providers", {}), dict) else {}
    health = _fetch_health()
    provider_access = health.get("provider_access", {}) if isinstance(health, dict) else {}
    verified_at = _now()

    for provider, info in provider_access.items():
        if not isinstance(info, dict):
            continue
        method = str(info.get("method") or "").strip()
        if not method or method == "disabled":
            continue
        provider_snapshot = providers.get(provider, {}) if isinstance(providers.get(provider), dict) else {}
        provider_snapshot.update({
            "overall_mode": info.get("status", provider_snapshot.get("overall_mode", "unknown")),
            "factual_state": info.get("status", provider_snapshot.get("factual_state", "unknown")),
            "last_verified_at": verified_at,
        })
        methods = provider_snapshot.get("methods", {}) if isinstance(provider_snapshot.get("methods", {}), dict) else {}
        method_snapshot = methods.get(method, {}) if isinstance(methods.get(method, {}), dict) else {}
        method_snapshot.update({
            "mode": info.get("status", method_snapshot.get("mode", "unknown")),
            "runtime_confirmed": bool(info.get("runtime_confirmed")),
            "last_verified_at": verified_at,
            "verification_basis": "aichaind_health_runtime",
        })
        if info.get("target_form_reached") is not None:
            method_snapshot["target_form_reached"] = bool(info.get("target_form_reached"))
        if info.get("project_verification"):
            provider_snapshot["project_verification"] = info.get("project_verification")
        methods[method] = method_snapshot
        provider_snapshot["methods"] = methods
        providers[provider] = provider_snapshot

    codex = OpenAICodexOAuthAdapter()
    codex_discovery = codex.discover()
    if codex_discovery.status == "authenticated":
        provider_snapshot = providers.get("openai-codex", {}) if isinstance(providers.get("openai-codex"), dict) else {}
        methods = provider_snapshot.get("methods", {}) if isinstance(provider_snapshot.get("methods", {}), dict) else {}
        oauth_snapshot = methods.get("oauth", {}) if isinstance(methods.get("oauth"), dict) else {}
        target_reached = bool(codex_discovery.limits.get("target_form_reached"))
        oauth_snapshot.update({
            "mode": "runtime_confirmed" if target_reached else "target_form_not_reached",
            "runtime_confirmed": True,
            "target_form_reached": target_reached,
            "verified_models": list(codex_discovery.available_models or []),
            "target_model": codex_discovery.limits.get("target_model"),
            "last_verified_at": verified_at,
            "verification_basis": "openclaw_gateway_runtime_probe",
        })
        methods["oauth"] = oauth_snapshot
        provider_snapshot.update({
            "overall_mode": "runtime_confirmed" if target_reached else "target_form_not_reached",
            "factual_state": "runtime_confirmed" if target_reached else "target_form_not_reached",
            "last_verified_at": verified_at,
            "project_verification": (
                "Maintainer runtime probe confirmed openai-codex/gpt-5.4 through the OpenClaw gateway."
                if target_reached else
                "Maintainer runtime probe authenticated the OAuth path, but target-form model confirmation still falls short."
            ),
            "runtime_confirmed_methods": ["oauth"],
        })
        provider_snapshot["methods"] = methods
        providers["openai-codex"] = provider_snapshot

    payload["generated_at"] = verified_at
    payload["providers"] = providers
    return payload


def main() -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_snapshot()
    SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[AIchain] Provider access runtime snapshot written to {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
