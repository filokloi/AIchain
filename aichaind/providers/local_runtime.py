#!/usr/bin/env python3
"""Utilities for detecting and selecting local OpenAI-compatible runtimes."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from aichaind.providers.adapters.local_openai import LOCAL_PROVIDER_CONFIG

DEFAULT_LOCAL_PROVIDER_ORDER = ["ollama", "lmstudio", "local", "vllm", "llamacpp"]
_NON_GENERATION_TOKENS = ("embed", "embedding", "rerank")


@dataclass
class LocalRuntimeProbe:
    provider: str
    base_url: str
    reachable: bool = False
    discovered_models: list[str] = field(default_factory=list)
    executable_present: bool = False
    executable_path: str = ""
    source: str = ""
    health_checked: bool = False
    error: str = ""
    completion_checked: bool = False
    completion_ready: bool = False
    completion_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LocalExecutionResolution:
    status: str
    enabled: bool
    provider: str
    model: str
    base_url: str
    reason: str
    probes: list[LocalRuntimeProbe] = field(default_factory=list)

    @property
    def adapter_present(self) -> bool:
        return bool(self.provider)

    @property
    def health_check_ok(self) -> bool:
        return any(probe.provider == self.provider and probe.reachable for probe in self.probes)


def detect_local_runtimes(
    providers: list[str] | None = None,
    timeout: float = 2.5,
    base_url_overrides: dict[str, str] | None = None,
) -> list[LocalRuntimeProbe]:
    candidates = []
    overrides = {str(k).strip().lower(): str(v).strip() for k, v in (base_url_overrides or {}).items() if str(k).strip() and str(v).strip()}
    for provider in providers or DEFAULT_LOCAL_PROVIDER_ORDER:
        normalized = str(provider or "").strip().lower()
        if normalized not in LOCAL_PROVIDER_CONFIG:
            continue
        if normalized in overrides:
            base_url, source = overrides[normalized], "config_override"
        else:
            base_url, source = resolve_local_base_url(normalized)
        executable_path = find_local_executable(normalized)
        probe = probe_local_runtime(normalized, base_url=base_url, timeout=timeout)
        probe.source = source
        probe.executable_path = executable_path
        probe.executable_present = bool(executable_path)
        candidates.append(probe)
    return candidates


def resolve_local_execution(local_cfg: dict, timeout: float = 2.5, detect_when_disabled: bool = False) -> LocalExecutionResolution:
    enabled = bool((local_cfg or {}).get("enabled"))
    configured_provider = str((local_cfg or {}).get("provider") or "local").strip().lower() or "local"
    explicit_model = str((local_cfg or {}).get("default_model") or "").strip()
    configured_base_url = str((local_cfg or {}).get("base_url") or "").strip()
    auto_detect = bool((local_cfg or {}).get("auto_detect", False)) or configured_provider == "auto"
    preferred_providers = [
        str(item).strip().lower()
        for item in ((local_cfg or {}).get("preferred_providers") or DEFAULT_LOCAL_PROVIDER_ORDER)
        if str(item).strip()
    ] or list(DEFAULT_LOCAL_PROVIDER_ORDER)

    if auto_detect:
        probe_targets = list(dict.fromkeys(
            ([configured_provider] if configured_provider != "auto" else []) + preferred_providers
        ))
    else:
        probe_targets = [configured_provider] if configured_provider != "auto" else list(preferred_providers)
    should_detect = enabled or detect_when_disabled or auto_detect
    base_url_overrides = {}
    if configured_provider != "auto" and configured_base_url:
        base_url_overrides[configured_provider] = configured_base_url
    probes = detect_local_runtimes(
        probe_targets,
        timeout=timeout,
        base_url_overrides=base_url_overrides,
    ) if should_detect else []

    if not enabled:
        return LocalExecutionResolution(
            status="disabled",
            enabled=False,
            provider="",
            model="",
            base_url=str((local_cfg or {}).get("base_url") or ""),
            reason="local_execution.disabled",
            probes=probes,
        )

    chosen_probe = None
    if configured_provider != "auto":
        chosen_probe = next((probe for probe in probes if probe.provider == configured_provider and probe.reachable), None)

    if chosen_probe is None and auto_detect:
        chosen_probe = select_best_local_runtime(
            probes,
            preferred_providers=preferred_providers,
            requested_model=explicit_model,
        )

    selected_provider = configured_provider if configured_provider != "auto" else (chosen_probe.provider if chosen_probe else "")
    selected_base_url = chosen_probe.base_url if chosen_probe else str((local_cfg or {}).get("base_url") or "")
    selected_model = normalize_local_model(explicit_model, chosen_probe.provider if chosen_probe else selected_provider)

    if chosen_probe and chosen_probe.discovered_models:
        discovered_lower = {str(item).strip().lower() for item in chosen_probe.discovered_models}
        if not selected_model or selected_model.lower() not in discovered_lower:
            selected_model = choose_preferred_local_model(
                chosen_probe.discovered_models,
                requested_model=explicit_model,
            )

    if not selected_model:
        reason = "local_execution.default_model missing"
        if auto_detect and probes:
            reason = "local_execution.default_model missing and no discovered model available"
        return LocalExecutionResolution(
            status="blocked_unconfigured",
            enabled=True,
            provider=selected_provider,
            model="",
            base_url=selected_base_url,
            reason=reason,
            probes=probes,
        )

    if chosen_probe and chosen_probe.reachable:
        return LocalExecutionResolution(
            status="runtime_confirmed",
            enabled=True,
            provider=chosen_probe.provider,
            model=selected_model,
            base_url=chosen_probe.base_url,
            reason="local runtime reachable",
            probes=probes,
        )

    return LocalExecutionResolution(
        status="configured_but_unreachable",
        enabled=True,
        provider=selected_provider,
        model=selected_model,
        base_url=selected_base_url,
        reason="local runtime health check failed",
        probes=probes,
    )


def select_best_local_runtime(
    probes: list[LocalRuntimeProbe],
    preferred_providers: list[str] | None = None,
    requested_model: str = "",
) -> LocalRuntimeProbe | None:
    preferred = preferred_providers or DEFAULT_LOCAL_PROVIDER_ORDER
    reachable = [probe for probe in probes if probe.reachable and probe.discovered_models]
    if not reachable:
        return None

    normalized_model = str(requested_model or "").strip().lower()
    if normalized_model:
        for provider in preferred:
            for probe in reachable:
                if probe.provider != provider:
                    continue
                if any(normalized_model in model.lower() for model in probe.discovered_models):
                    return probe

    for provider in preferred:
        for probe in reachable:
            if probe.provider == provider:
                return probe
    return reachable[0]


def choose_preferred_local_model(models: list[str], requested_model: str = "") -> str:
    normalized_request = str(requested_model or "").strip().lower()
    if normalized_request:
        for model in models or []:
            if normalized_request in str(model).lower():
                return str(model)

    generative = [model for model in (models or []) if is_generation_model(model)]
    if generative:
        return str(generative[0])
    return str((models or [""])[0] or "")


def iter_local_model_candidates(
    provider: str,
    discovered_models: list[str],
    requested_model: str = "",
    resolved_model: str = "",
) -> list[str]:
    candidates: list[str] = []

    def _append(model: str) -> None:
        normalized = str(model or "").strip()
        if not normalized or not is_generation_model(normalized):
            return
        lowered = normalized.lower()
        if any(existing.lower() == lowered for existing in candidates):
            return
        candidates.append(normalized)

    _append(normalize_local_model(resolved_model, provider))
    _append(normalize_local_model(requested_model, provider))
    _append(choose_preferred_local_model(discovered_models, requested_model=requested_model))
    for model in discovered_models or []:
        _append(model)
    return candidates


def is_generation_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    return not any(token in normalized for token in _NON_GENERATION_TOKENS)


def normalize_local_model(model: str, provider: str) -> str:
    normalized_model = str(model or "").strip()
    normalized_provider = str(provider or "").strip().lower()
    if not normalized_model:
        return ""
    if "/" in normalized_model:
        prefix, rest = normalized_model.split("/", 1)
        if prefix.lower() in LOCAL_PROVIDER_CONFIG:
            if normalized_provider and prefix.lower() != normalized_provider:
                return f"{normalized_provider}/{rest}"
            return normalized_model
        return f"{normalized_provider}/{normalized_model}" if normalized_provider else normalized_model
    return f"{normalized_provider}/{normalized_model}" if normalized_provider else normalized_model


def resolve_local_base_url(provider: str) -> tuple[str, str]:
    config = LOCAL_PROVIDER_CONFIG.get(provider, LOCAL_PROVIDER_CONFIG["local"])
    for key in config["env_keys"]:
        value = os.environ.get(key, "")
        if value:
            return value, f"env:{key}"
    return config["default_base_url"], "default"


def probe_local_runtime(provider: str, base_url: str = "", timeout: float = 2.5) -> LocalRuntimeProbe:
    resolved_base_url, source = resolve_local_base_url(provider)
    target_base_url = str(base_url or resolved_base_url or "").strip()
    probe = LocalRuntimeProbe(provider=provider, base_url=target_base_url, source=source)
    if not requests or not target_base_url:
        probe.error = "requests unavailable" if not requests else "missing base_url"
        return probe

    try:
        resp = requests.get(f"{target_base_url}/models", timeout=timeout)
        probe.health_checked = True
        if resp.status_code != 200:
            probe.error = f"HTTP {resp.status_code}"
            return probe
        payload = resp.json().get("data", [])
        probe.discovered_models = [
            f"{provider}/{item['id']}"
            for item in payload
            if isinstance(item, dict) and item.get("id")
        ]
        probe.reachable = bool(probe.discovered_models)
        if not probe.discovered_models:
            probe.error = "no models discovered"
        return probe
    except Exception as exc:
        probe.health_checked = True
        probe.error = str(exc)
        return probe


def probe_local_completion(provider: str, model: str, base_url: str = "", timeout: float = 45.0) -> tuple[bool, str]:
    resolved_base_url, _ = resolve_local_base_url(provider)
    target_base_url = str(base_url or resolved_base_url or "").strip()
    normalized_model = normalize_local_model(model, provider)
    if not requests or not target_base_url:
        return False, "requests unavailable" if not requests else "missing base_url"
    if not normalized_model:
        return False, "missing model"

    request_model = normalized_model.split("/", 1)[1] if "/" in normalized_model and normalized_model.split("/", 1)[0] in LOCAL_PROVIDER_CONFIG else normalized_model
    payload = {
        "model": request_model,
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    }
    try:
        resp = requests.post(f"{target_base_url}/chat/completions", json=payload, timeout=timeout)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            return False, "no choices returned"
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            return False, "empty completion content"
        return True, content[:200]
    except Exception as exc:
        return False, str(exc)


def find_local_executable(provider: str) -> str:
    candidates = []
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    user_home = str(Path.home())
    if provider == "lmstudio":
        candidates.extend([
            Path(local_appdata) / "Programs" / "LM Studio" / "LM Studio.exe",
            Path(user_home) / ".lmstudio" / "bin" / "lms.exe",
        ])
        which = shutil.which("lms")
        if which:
            candidates.append(Path(which))
    elif provider == "ollama":
        candidates.extend([
            Path(local_appdata) / "Programs" / "Ollama" / "ollama.exe",
            Path(local_appdata) / "Programs" / "Ollama" / "ollama app.exe",
        ])
        which = shutil.which("ollama")
        if which:
            candidates.append(Path(which))
    elif provider == "vllm":
        which = shutil.which("vllm") or shutil.which("vllm.exe")
        if which:
            candidates.append(Path(which))
    elif provider == "llamacpp":
        which = shutil.which("llama-server") or shutil.which("server")
        if which:
            candidates.append(Path(which))

    for candidate in candidates:
        try:
            if candidate and candidate.exists():
                return str(candidate)
        except OSError:
            continue
    return ""
