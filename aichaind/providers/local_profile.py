#!/usr/bin/env python3
"""Local runtime profiling and storage for self-hosted execution paths."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from aichaind.core.state_machine import atomic_write, safe_read_json
from aichaind.providers.local_runtime import normalize_local_model, resolve_local_base_url

_TOTAL_MEMORY_RE = re.compile(r"Estimated Total Memory:\s+([0-9.]+)\s+GiB", re.IGNORECASE)
_CAPACITY_BLOCK_RE = re.compile(r"requires approximately .*? memory|will fail to load based on your resource guardrails settings|insufficient system resources", re.IGNORECASE | re.DOTALL)
_CAPACITY_OK_RE = re.compile(r"Estimate:\s+This model may be loaded", re.IGNORECASE)


@dataclass
class LocalTaskProfile:
    name: str
    success: bool
    latency_ms: float
    ttft_ms: float | None = None
    completion_tokens: int = 0
    tokens_per_second: float | None = None
    suitability_score: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalModelProfile:
    provider: str
    model: str
    base_url: str
    profiled_at: str
    runtime_confirmed: bool
    task_profiles: dict[str, LocalTaskProfile] = field(default_factory=dict)
    success_rate: float = 0.0
    average_latency_ms: float = 0.0
    average_ttft_ms: float | None = None
    average_tokens_per_second: float | None = None
    speed_score: float = 0.0
    stability_score: float = 0.0
    safe_timeout_ms: int = 45000
    capacity_status: str = "not_checked"
    capacity_detail: str = ""
    estimated_total_memory_gib: float | None = None
    prompt_type_suitability: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task_profiles"] = {name: profile.to_dict() for name, profile in self.task_profiles.items()}
        return payload


@dataclass
class CapacityEstimate:
    status: str
    detail: str
    estimated_total_memory_gib: float | None = None


class LocalProfileStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        return safe_read_json(self.path, default={"version": 1, "profiles": {}}) or {"version": 1, "profiles": {}}

    def get(self, model_id: str) -> dict[str, Any] | None:
        data = self.snapshot()
        return data.get("profiles", {}).get(model_id)

    def upsert(self, profile: LocalModelProfile) -> dict[str, Any]:
        data = self.snapshot()
        profiles = data.setdefault("profiles", {})
        profiles[profile.model] = profile.to_dict()
        data["version"] = 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write(self.path, data)
        return data

    def summary(self, active_model: str = "") -> dict[str, Any]:
        data = self.snapshot()
        profiles = data.get("profiles", {})
        active = profiles.get(active_model) if active_model else None
        return {
            "store_path": str(self.path),
            "total_profiles": len(profiles),
            "updated_at": data.get("updated_at"),
            "active_model": active_model,
            "active_profile": active,
            "models": sorted(profiles.keys()),
        }


def parse_capacity_output(text: str) -> CapacityEstimate:
    normalized = str(text or "").strip()
    estimated = None
    match = _TOTAL_MEMORY_RE.search(normalized)
    if match:
        try:
            estimated = float(match.group(1))
        except ValueError:
            estimated = None
    if _CAPACITY_BLOCK_RE.search(normalized):
        return CapacityEstimate("machine_capacity_blocked", normalized, estimated)
    if _CAPACITY_OK_RE.search(normalized):
        return CapacityEstimate("capacity_ok", normalized, estimated)
    return CapacityEstimate("unknown", normalized, estimated)


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
    except Exception as exc:  # pragma: no cover
        return CapacityEstimate("unknown", str(exc), None)

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    combined = "\n".join(part for part in [output, error] if part).strip()
    estimate = parse_capacity_output(combined)
    if estimate.status == "unknown" and result.returncode == 0:
        return CapacityEstimate("capacity_ok", combined, estimate.estimated_total_memory_gib)
    return estimate


def _extract_completion_tokens(body: dict[str, Any], content: str) -> int:
    usage = body.get("usage") or {}
    if isinstance(usage.get("completion_tokens"), int):
        return int(usage["completion_tokens"])
    rough = max(1, len(content.split())) if content else 0
    return rough


def _stream_ttft(base_url: str, payload: dict[str, Any], timeout: float) -> tuple[float | None, str]:
    if not requests:
        return None, "requests unavailable"
    stream_payload = dict(payload)
    stream_payload["stream"] = True
    start = time.perf_counter()
    try:
        with requests.post(f"{base_url}/chat/completions", json=stream_payload, timeout=timeout, stream=True) as resp:
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            for raw_line in resp.iter_lines(decode_unicode=True):
                line = str(raw_line or "").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                delta = choice.get("delta") or {}
                text = delta.get("content") or choice.get("text") or ""
                if text or delta.get("role"):
                    return round((time.perf_counter() - start) * 1000.0, 2), "runtime_stream_probe"
    except Exception as exc:
        return None, str(exc)
    return None, "no_first_token"


def _run_probe(base_url: str, payload: dict[str, Any], timeout: float) -> tuple[bool, str, float, int, str]:
    if not requests:
        return False, "", 0.0, 0, "requests unavailable"
    start = time.perf_counter()
    try:
        resp = requests.post(f"{base_url}/chat/completions", json=payload, timeout=timeout)
        elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
        if resp.status_code != 200:
            return False, "", elapsed_ms, 0, f"HTTP {resp.status_code}"
        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            return False, "", elapsed_ms, 0, "no choices returned"
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        tokens = _extract_completion_tokens(body, content)
        return True, content, elapsed_ms, tokens, "ok"
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
        return False, "", elapsed_ms, 0, str(exc)


def _validate_general_chat(content: str) -> tuple[bool, float, str]:
    ok = "LOCAL_PROFILE_OK" in (content or "")
    return ok, 100.0 if ok else 10.0, content[:200]


def _validate_reasoning(content: str) -> tuple[bool, float, str]:
    ok = bool(re.search(r"\b45\b", content or ""))
    return ok, 100.0 if ok else 20.0, content[:200]


def _validate_coding(content: str) -> tuple[bool, float, str]:
    lower = (content or "").lower()
    ok = "def add" in lower and "return" in lower
    return ok, 100.0 if ok else 25.0, content[:200]


def _validate_structured_output(content: str) -> tuple[bool, float, str]:
    try:
        payload = json.loads(content)
        ok = payload.get("ok") is True and payload.get("answer") == 7
        return ok, 100.0 if ok else 35.0, content[:200]
    except Exception:
        return False, 15.0, content[:200]


_TASK_PROBES: list[tuple[str, str, int, Callable[[str], tuple[bool, float, str]]]] = [
    ("general_chat", "Reply with exactly LOCAL_PROFILE_OK.", 12, _validate_general_chat),
    ("reasoning", "What is 17 + 28? Reply with digits only.", 12, _validate_reasoning),
    ("coding", "Return only Python code for a function add(a, b) that returns a + b.", 80, _validate_coding),
    ("structured_output", 'Return only minified JSON: {"ok":true,"answer":7}', 40, _validate_structured_output),
]


def profile_local_model(provider: str, model: str, base_url: str = "", timeout: float = 45.0) -> LocalModelProfile:
    normalized_provider = str(provider or "").strip().lower()
    resolved_base_url, _ = resolve_local_base_url(normalized_provider)
    target_base_url = str(base_url or resolved_base_url or "").strip()
    normalized_model = normalize_local_model(model, normalized_provider)
    request_model = normalized_model.split("/", 1)[1] if "/" in normalized_model and normalized_model.split("/", 1)[0] == normalized_provider else normalized_model

    task_profiles: dict[str, LocalTaskProfile] = {}
    success_count = 0
    total_latency = 0.0
    total_ttft = 0.0
    ttft_count = 0
    total_tps = 0.0
    tps_count = 0
    max_latency = 0.0

    for task_name, prompt, max_tokens, validator in _TASK_PROBES:
        payload = {
            "model": request_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        ttft_ms, ttft_detail = _stream_ttft(target_base_url, payload, min(timeout, 20.0))
        ok, content, latency_ms, completion_tokens, detail = _run_probe(target_base_url, payload, timeout)
        valid, suitability, excerpt = validator(content if ok else detail)
        success = ok and valid
        tokens_per_second = None
        if ok and completion_tokens > 0 and latency_ms > 0:
            tokens_per_second = round(completion_tokens / max(latency_ms / 1000.0, 0.001), 3)
        task_profiles[task_name] = LocalTaskProfile(
            name=task_name,
            success=success,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            completion_tokens=completion_tokens,
            tokens_per_second=tokens_per_second,
            suitability_score=suitability,
            detail=excerpt if success else f"{detail}; {ttft_detail}"[:200],
        )
        if success:
            success_count += 1
        total_latency += latency_ms
        max_latency = max(max_latency, latency_ms)
        if ttft_ms is not None:
            total_ttft += ttft_ms
            ttft_count += 1
        if tokens_per_second is not None:
            total_tps += tokens_per_second
            tps_count += 1

    task_count = max(len(task_profiles), 1)
    success_rate = round(success_count / task_count, 4)
    average_latency_ms = round(total_latency / task_count, 2)
    average_ttft_ms = round(total_ttft / ttft_count, 2) if ttft_count else None
    average_tps = round(total_tps / tps_count, 3) if tps_count else None
    ttft_component = 60.0
    if average_ttft_ms is not None:
        if average_ttft_ms <= 600:
            ttft_component = 96.0
        elif average_ttft_ms <= 1500:
            ttft_component = 82.0
        elif average_ttft_ms <= 4000:
            ttft_component = 62.0
        else:
            ttft_component = 42.0
    tps_component = min(100.0, (average_tps or 0.0) * 8.0) if average_tps is not None else 55.0
    speed_score = round((ttft_component * 0.55) + (tps_component * 0.45), 2)
    stability_score = round(success_rate * 100.0, 2)
    safe_timeout_ms = int(max(20000.0, max_latency * 3.0))
    capacity = estimate_lmstudio_capacity(normalized_model) if normalized_provider == "lmstudio" else CapacityEstimate("not_checked", "capacity probe unavailable for provider", None)
    if success_count > 0 and capacity.status == "machine_capacity_blocked":
        capacity = CapacityEstimate(
            "capacity_estimate_conflict",
            f"Runtime probes succeeded despite estimate warning. {capacity.detail}",
            capacity.estimated_total_memory_gib,
        )

    return LocalModelProfile(
        provider=normalized_provider,
        model=normalized_model,
        base_url=target_base_url,
        profiled_at=datetime.now(timezone.utc).isoformat(),
        runtime_confirmed=success_rate > 0,
        task_profiles=task_profiles,
        success_rate=success_rate,
        average_latency_ms=average_latency_ms,
        average_ttft_ms=average_ttft_ms,
        average_tokens_per_second=average_tps,
        speed_score=speed_score,
        stability_score=stability_score,
        safe_timeout_ms=safe_timeout_ms,
        capacity_status=capacity.status,
        capacity_detail=capacity.detail,
        estimated_total_memory_gib=capacity.estimated_total_memory_gib,
        prompt_type_suitability={name: round(profile.suitability_score, 2) for name, profile in task_profiles.items()},
    )
