#!/usr/bin/env python3
"""Verify local_execution runtime readiness on the current machine."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aichaind.core.state_machine import load_config
from aichaind.providers.registry import get_adapter


@dataclass
class LocalExecutionStatus:
    status: str
    enabled: bool
    provider: str
    model: str
    adapter_present: bool
    health_check_ok: bool
    reasons: list[str]


def classify_local_execution_status(enabled: bool, adapter_present: bool, health_check_ok: bool, model: str) -> LocalExecutionStatus:
    reasons: list[str] = []
    provider = ""
    if "/" in model:
        provider = model.split("/", 1)[0]

    if not enabled:
        reasons.append("local_execution.disabled")
        return LocalExecutionStatus("disabled", enabled, provider, model, adapter_present, health_check_ok, reasons)
    if not model:
        reasons.append("local_execution.default_model missing")
        return LocalExecutionStatus("blocked_unconfigured", enabled, provider, model, adapter_present, health_check_ok, reasons)
    if not adapter_present:
        reasons.append("local adapter missing")
        return LocalExecutionStatus("blocked_unconfigured", enabled, provider, model, adapter_present, health_check_ok, reasons)
    if not health_check_ok:
        reasons.append("local runtime health check failed")
        return LocalExecutionStatus("configured_but_unreachable", enabled, provider, model, adapter_present, health_check_ok, reasons)
    return LocalExecutionStatus("runtime_confirmed", enabled, provider, model, adapter_present, health_check_ok, reasons)


def main() -> int:
    cfg = load_config(REPO_ROOT / "config" / "default.json")
    local_cfg = cfg.get("local_execution", {})
    enabled = bool(local_cfg.get("enabled"))
    provider = str(local_cfg.get("provider") or "local").lower()
    model = str(local_cfg.get("default_model") or "").strip()
    adapter = get_adapter(provider) if enabled else None
    health_ok = False
    if adapter:
        try:
            health_ok = bool(adapter.health_check())
        except Exception:
            health_ok = False

    result = classify_local_execution_status(enabled, adapter is not None, health_ok, model)
    payload = asdict(result)
    payload["configured_provider"] = provider
    payload["configured_base_url"] = local_cfg.get("base_url", "")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.status == "runtime_confirmed" else 1


if __name__ == "__main__":
    sys.exit(main())
