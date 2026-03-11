#!/usr/bin/env python3
"""Configure local_execution via a user-local override after real runtime verification."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aichaind.core.state_machine import load_config, resolve_path
from aichaind.providers.local_runtime import (
    iter_local_model_candidates,
    probe_local_completion,
    resolve_local_execution,
    select_best_local_runtime,
)


def _override_path() -> Path:
    env_override = os.environ.get("AICHAIND_CONFIG_OVERRIDE", "")
    if env_override:
        return resolve_path(env_override)
    return resolve_path("~/.openclaw/aichain/config.local.json")


def _build_local_override(local_cfg: dict, provider: str, base_url: str, model: str) -> dict:
    preferred = [provider]
    for item in local_cfg.get("preferred_providers") or []:
        normalized = str(item).strip().lower()
        if normalized and normalized not in preferred:
            preferred.append(normalized)
    return {
        "local_execution": {
            "enabled": True,
            "provider": provider,
            "base_url": base_url,
            "default_model": model,
            "require_healthcheck": bool(local_cfg.get("require_healthcheck", True)),
            "auto_detect": bool(local_cfg.get("auto_detect", True)),
            "preferred_providers": preferred,
        }
    }


def main() -> int:
    cfg = load_config(REPO_ROOT / "config" / "default.json")
    local_cfg = cfg.get("local_execution", {})
    resolution = resolve_local_execution(local_cfg, timeout=2.5, detect_when_disabled=True)
    preferred = [
        str(item).strip().lower()
        for item in (local_cfg.get("preferred_providers") or [])
        if str(item).strip()
    ]

    selected_probe = select_best_local_runtime(
        resolution.probes,
        preferred_providers=preferred or None,
        requested_model=str(local_cfg.get("default_model") or ""),
    )
    if not selected_probe:
        print(json.dumps({
            "status": "target_form_not_reached",
            "reason": "no reachable local runtime detected",
            "detected_runtimes": [probe.to_dict() for probe in resolution.probes],
        }, indent=2, ensure_ascii=False))
        return 1

    candidate_models = iter_local_model_candidates(
        selected_probe.provider,
        selected_probe.discovered_models,
        requested_model=str(local_cfg.get("default_model") or ""),
        resolved_model=resolution.model if resolution.provider == selected_probe.provider else "",
    )
    if not candidate_models:
        print(json.dumps({
            "status": "blocked_unconfigured",
            "reason": "no chat-capable local model discovered",
            "provider": selected_probe.provider,
            "base_url": selected_probe.base_url,
            "detected_models": selected_probe.discovered_models,
        }, indent=2, ensure_ascii=False))
        return 1

    model = ""
    ok = False
    probe_detail = ""
    probe_attempts: list[dict[str, str | bool]] = []
    for candidate_model in candidate_models:
        ok, probe_detail = probe_local_completion(
            selected_probe.provider,
            candidate_model,
            base_url=selected_probe.base_url,
            timeout=90.0,
        )
        probe_attempts.append({
            "model": candidate_model,
            "ok": ok,
            "detail": probe_detail,
        })
        if ok:
            model = candidate_model
            break

    selected_probe.completion_checked = True
    selected_probe.completion_ready = ok
    selected_probe.completion_error = "" if ok else probe_detail

    if not ok:
        print(json.dumps({
            "status": "target_form_not_reached",
            "reason": "local completion probe failed",
            "provider": selected_probe.provider,
            "base_url": selected_probe.base_url,
            "model_candidates": candidate_models,
            "probe_error": probe_detail,
            "probe_attempts": probe_attempts,
            "detected_runtimes": [probe.to_dict() for probe in resolution.probes],
        }, indent=2, ensure_ascii=False))
        return 1

    override = _build_local_override(local_cfg, selected_probe.provider, selected_probe.base_url, model)
    override_path = _override_path()
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(json.dumps(override, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "runtime_confirmed",
        "provider": selected_probe.provider,
        "base_url": selected_probe.base_url,
        "model": model,
        "probe_attempts": probe_attempts,
        "override_path": str(override_path),
        "probe_result": probe_detail,
        "config_sources_after_restart": cfg.get("_config_sources", []) + [str(override_path)],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
