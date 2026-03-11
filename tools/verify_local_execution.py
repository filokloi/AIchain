#!/usr/bin/env python3
"""Verify local_execution runtime readiness on the current machine."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aichaind.core.state_machine import load_config
from aichaind.providers.registry import get_adapter
from aichaind.providers.local_runtime import (
    iter_local_model_candidates,
    probe_local_completion,
    resolve_local_execution,
    select_best_local_runtime,
)

_TOTAL_MEMORY_RE = re.compile(r"Estimated Total Memory:\s+([0-9.]+)\s+GiB", re.IGNORECASE)
_CAPACITY_BLOCK_RE = re.compile(r"requires approximately .*? memory|will fail to load based on your resource guardrails settings|insufficient system resources", re.IGNORECASE | re.DOTALL)
_CAPACITY_OK_RE = re.compile(r"Estimate:\s+This model may be loaded", re.IGNORECASE)


@dataclass
class LocalExecutionStatus:
    status: str
    enabled: bool
    provider: str
    model: str
    adapter_present: bool
    health_check_ok: bool
    reasons: list[str]


@dataclass
class LocalExecutionReadiness:
    effective_status: str
    activation_ready: bool
    reasons: list[str]
    completion_probe_ok: bool | None
    completion_probe_detail: str
    completion_probe_provider: str
    completion_probe_model: str
    capacity_status: str
    capacity_detail: str
    estimated_total_memory_gib: float | None


@dataclass
class CapacityEstimate:
    status: str
    detail: str
    estimated_total_memory_gib: float | None


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


def parse_lmstudio_estimate_output(text: str) -> CapacityEstimate:
    normalized = str(text or "").strip()
    estimated = None
    match = _TOTAL_MEMORY_RE.search(normalized)
    if match:
        try:
            estimated = float(match.group(1))
        except ValueError:
            estimated = None

    if _CAPACITY_BLOCK_RE.search(normalized):
        return CapacityEstimate(
            status="machine_capacity_blocked",
            detail=normalized,
            estimated_total_memory_gib=estimated,
        )
    if _CAPACITY_OK_RE.search(normalized):
        return CapacityEstimate(
            status="capacity_ok",
            detail=normalized,
            estimated_total_memory_gib=estimated,
        )
    return CapacityEstimate(
        status="unknown",
        detail=normalized,
        estimated_total_memory_gib=estimated,
    )


def estimate_lmstudio_capacity(model: str) -> CapacityEstimate:
    normalized = str(model or "").strip()
    if not normalized:
        return CapacityEstimate("not_checked", "missing model", None)
    model_id = normalized.split("/", 1)[1] if normalized.startswith("lmstudio/") else normalized
    command = [
        str(Path.home() / ".lmstudio" / "bin" / "lms.exe"),
        "load",
        model_id,
        "--estimate-only",
        "--gpu",
        "off",
        "-c",
        "512",
        "--parallel",
        "1",
        "-y",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except FileNotFoundError:
        return CapacityEstimate("not_checked", "lms executable not found", None)
    except Exception as exc:  # pragma: no cover - defensive guard for local env
        return CapacityEstimate("unknown", str(exc), None)

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    combined = "\n".join(part for part in [output, error] if part).strip()
    estimate = parse_lmstudio_estimate_output(combined)
    if estimate.status == "unknown" and result.returncode == 0:
        return CapacityEstimate("capacity_ok", combined, estimate.estimated_total_memory_gib)
    return estimate


def refine_local_execution_readiness(
    status: LocalExecutionStatus,
    completion_probe_ok: bool | None,
    completion_probe_detail: str = "",
    completion_probe_provider: str = "",
    completion_probe_model: str = "",
    capacity_estimate: CapacityEstimate | None = None,
) -> LocalExecutionReadiness:
    reasons = list(status.reasons)
    effective_status = status.status
    activation_ready = False
    capacity = capacity_estimate or CapacityEstimate("not_checked", "", None)

    if status.status == "runtime_confirmed" and completion_probe_ok is False:
        effective_status = "target_form_not_reached"
        reasons.append("local completion probe failed")
    elif status.status == "disabled" and completion_probe_ok is True:
        activation_ready = True
        reasons.append("local runtime activation candidate ready")

    if capacity.status == "machine_capacity_blocked":
        if "machine_capacity_blocked" not in reasons:
            reasons.append("machine_capacity_blocked")
        if effective_status == "runtime_confirmed" and completion_probe_ok is not True:
            effective_status = "target_form_not_reached"

    return LocalExecutionReadiness(
        effective_status=effective_status,
        activation_ready=activation_ready,
        reasons=reasons,
        completion_probe_ok=completion_probe_ok,
        completion_probe_detail=completion_probe_detail,
        completion_probe_provider=completion_probe_provider,
        completion_probe_model=completion_probe_model,
        capacity_status=capacity.status,
        capacity_detail=capacity.detail,
        estimated_total_memory_gib=capacity.estimated_total_memory_gib,
    )


def _select_completion_candidate(local_cfg: dict, resolution):
    requested_model = str(local_cfg.get("default_model") or "")
    preferred = [
        str(item).strip().lower()
        for item in (local_cfg.get("preferred_providers") or [])
        if str(item).strip()
    ]
    selected_probe = select_best_local_runtime(
        resolution.probes,
        preferred_providers=preferred or None,
        requested_model=requested_model,
    )
    if not selected_probe:
        return None, []
    candidates = iter_local_model_candidates(
        selected_probe.provider,
        selected_probe.discovered_models,
        requested_model=requested_model,
        resolved_model=resolution.model if resolution.provider == selected_probe.provider else "",
    )
    return selected_probe, candidates


def main() -> int:
    cfg = load_config(REPO_ROOT / "config" / "default.json")
    local_cfg = cfg.get("local_execution", {})
    resolution = resolve_local_execution(local_cfg, timeout=2.0, detect_when_disabled=True)
    adapter = get_adapter(resolution.provider) if resolution.provider else None
    result = classify_local_execution_status(
        resolution.enabled,
        adapter is not None,
        resolution.health_check_ok,
        resolution.model,
    )

    completion_probe_ok = None
    completion_probe_detail = ""
    completion_probe_provider = ""
    completion_probe_model = ""
    selected_probe, candidate_models = _select_completion_candidate(local_cfg, resolution)
    if selected_probe and candidate_models:
        completion_probe_provider = selected_probe.provider
        for candidate_model in candidate_models:
            completion_probe_model = candidate_model
            completion_probe_ok, completion_probe_detail = probe_local_completion(
                selected_probe.provider,
                candidate_model,
                base_url=selected_probe.base_url,
                timeout=20.0,
            )
            if completion_probe_ok:
                break

    capacity_estimate = None
    if selected_probe and selected_probe.provider == "lmstudio" and completion_probe_model:
        capacity_estimate = estimate_lmstudio_capacity(completion_probe_model)

    readiness = refine_local_execution_readiness(
        result,
        completion_probe_ok=completion_probe_ok,
        completion_probe_detail=completion_probe_detail,
        completion_probe_provider=completion_probe_provider,
        completion_probe_model=completion_probe_model,
        capacity_estimate=capacity_estimate,
    )

    payload = asdict(result)
    payload["effective_status"] = readiness.effective_status
    payload["activation_ready"] = readiness.activation_ready
    payload["reasons"] = readiness.reasons
    payload["completion_probe_ok"] = readiness.completion_probe_ok
    payload["completion_probe_detail"] = readiness.completion_probe_detail
    payload["completion_probe_provider"] = readiness.completion_probe_provider
    payload["completion_probe_model"] = readiness.completion_probe_model
    payload["capacity_status"] = readiness.capacity_status
    payload["capacity_detail"] = readiness.capacity_detail
    payload["estimated_total_memory_gib"] = readiness.estimated_total_memory_gib
    payload["configured_provider"] = str(local_cfg.get("provider") or "local").lower()
    payload["configured_base_url"] = local_cfg.get("base_url", "")
    payload["resolved_provider"] = resolution.provider
    payload["resolved_base_url"] = resolution.base_url
    payload["resolution_reason"] = resolution.reason
    payload["config_sources"] = cfg.get("_config_sources", [])
    payload["detected_runtimes"] = [probe.to_dict() for probe in resolution.probes]
    if not resolution.enabled and any(probe.reachable for probe in resolution.probes):
        payload.setdefault("reasons", []).append("reachable_local_runtimes_detected")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if readiness.effective_status == "runtime_confirmed" else 1


if __name__ == "__main__":
    sys.exit(main())
