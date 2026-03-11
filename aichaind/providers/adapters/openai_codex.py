#!/usr/bin/env python3
"""
aichaind.providers.adapters.openai_codex — OpenAI Codex OAuth Adapter

Executes Codex OAuth-backed models through the local OpenClaw Gateway HTTP
compatibility endpoint. The adapter is intentionally conservative:
- it never assumes GPT-5.4 exists locally
- it prefers GPT-5.4 when the local OpenClaw runtime exposes it
- otherwise it falls back to the best runtime-confirmed Codex model
- if the gateway bridge is disabled or unavailable, AIchain keeps existing
  API key / local / ranked-model fallback paths alive
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

from aichaind.providers.base import (
    CompletionRequest,
    CompletionResponse,
    DiscoveryResult,
    ProviderAdapter,
)

log = logging.getLogger("aichaind.providers.openai_codex")

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
OPENCLAW_AUTH_PROFILES = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
OPENCLAW_NPM_DIR = Path.home() / "AppData" / "Roaming" / "npm"
TARGET_MODEL = "openai-codex/gpt-5.4"
DEFAULT_FALLBACK_MODEL = "openai-codex/gpt-5.3-codex"


class OpenAICodexOAuthAdapter(ProviderAdapter):
    """Bridge OpenClaw's verified Codex OAuth runtime into AIchain."""

    def __init__(
        self,
        gateway_base_url: str = "",
        gateway_token: str = "",
        config_path: str | Path | None = None,
        auth_profiles_path: str | Path | None = None,
    ):
        self.config_path = Path(config_path).expanduser() if config_path else OPENCLAW_CONFIG
        self.auth_profiles_path = Path(auth_profiles_path).expanduser() if auth_profiles_path else OPENCLAW_AUTH_PROFILES
        self._config = _load_json_file(self.config_path)
        self._gateway_base_url = gateway_base_url or _resolve_gateway_http_base(self._config)
        self._gateway_token = gateway_token or _resolve_gateway_token(self._config)
        self._chat_endpoint_enabled = _resolve_gateway_endpoint_enabled(self._config, "chatCompletions")
        self._responses_endpoint_enabled = _resolve_gateway_endpoint_enabled(self._config, "responses")
        self._profile_id, self._profile = _load_codex_profile(self.auth_profiles_path)
        self._timeout = 90
        self._last_discovered_models: list[str] = []
        self._last_discovery_source = ""
        self._probe_ttl_seconds = 900
        self._target_probe_cache: tuple[bool, float, str] = (False, 0.0, "")
        super().__init__(name="openai-codex", api_key=self._gateway_token, access_methods={"oauth"})

    @property
    def gateway_base_url(self) -> str:
        return self._gateway_base_url

    @property
    def gateway_ready(self) -> bool:
        return bool(self.api_key and self._chat_endpoint_enabled and self.gateway_base_url)

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-OpenClaw-Token"] = self.api_key
        return headers

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        result.limits = {
            "provider": "openai-codex",
            "target_model": TARGET_MODEL,
            "gateway_base_url": self.gateway_base_url,
            "chat_endpoint_enabled": self._chat_endpoint_enabled,
            "responses_endpoint_enabled": self._responses_endpoint_enabled,
            "oauth_profile": self._profile_id,
        }

        if not requests:
            result.status = "error"
            result.limits["reason"] = "requests_not_installed"
            return result

        if not self._profile:
            result.status = "unconfigured"
            result.limits["reason"] = "oauth_profile_missing"
            return result

        models, source = self._discover_models()
        target_probe_ok, target_probe_reason = self._probe_target_model()
        if target_probe_ok:
            models = self._merge_models_with_target(models, TARGET_MODEL)
            source = f"{source}+runtime_probe" if source else "runtime_probe"
        result.available_models = models
        result.limits["model_source"] = source
        result.limits["preferred_model"] = self.resolve_preferred_model(TARGET_MODEL, models)
        result.limits["verified_models"] = list(models)
        result.limits["target_form_reached"] = TARGET_MODEL in models
        result.limits["target_probe_status"] = "ok" if target_probe_ok else "unverified"
        if target_probe_reason:
            result.limits["target_probe_reason"] = target_probe_reason

        if not models:
            result.status = "auth_failed"
            result.limits["reason"] = "no_codex_models_visible"
            return result

        if not self.gateway_base_url or not self.api_key:
            result.status = "error"
            result.limits["reason"] = "gateway_http_auth_missing"
            return result

        if not self._chat_endpoint_enabled:
            result.status = "error"
            result.limits["reason"] = "gateway_http_chat_completions_disabled"
            return result

        result.status = "authenticated"
        result.cost_mode = "subscription-window"
        return result

    def execute(self, request: CompletionRequest) -> CompletionResponse:
        if not requests:
            return CompletionResponse(model=request.model, content="", error="requests not installed", status="error")
        if not self.circuit_breaker.is_available:
            return CompletionResponse(model=request.model, content="", error="circuit breaker open", status="error")
        if not self.gateway_ready:
            return CompletionResponse(model=request.model, content="", error="openclaw gateway codex bridge unavailable", status="error")

        models, _ = self._discover_models()
        if self._should_try_target_model(request.model):
            target_probe_ok, _ = self._probe_target_model()
            if target_probe_ok:
                models = self._merge_models_with_target(models, TARGET_MODEL)
        selected_model = self.resolve_preferred_model(request.model, models)
        if not selected_model:
            return CompletionResponse(model=request.model, content="", error="no_openai_codex_model_available", status="error")

        payload = {
            "model": selected_model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False,
        }

        start_t = time.time()
        try:
            resp = requests.post(
                f"{self.gateway_base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
            latency = (time.time() - start_t) * 1000
            if resp.status_code != 200:
                self.circuit_breaker.record_failure()
                return CompletionResponse(
                    model=selected_model,
                    content="",
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    status="error",
                    latency_ms=latency,
                )

            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            usage = data.get("usage") or {}
            self.circuit_breaker.record_success()
            return CompletionResponse(
                model=selected_model,
                content=choice.get("message", {}).get("content", ""),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                finish_reason=choice.get("finish_reason", ""),
                latency_ms=latency,
                raw_response=data,
                status="success",
            )
        except requests.Timeout:
            self.circuit_breaker.record_failure()
            return CompletionResponse(
                model=selected_model,
                content="",
                error="timeout",
                status="timeout",
                latency_ms=(time.time() - start_t) * 1000,
            )
        except Exception as exc:
            self.circuit_breaker.record_failure()
            return CompletionResponse(
                model=selected_model,
                content="",
                error=str(exc),
                status="error",
                latency_ms=(time.time() - start_t) * 1000,
            )

    def health_check(self) -> bool:
        result = self.discover()
        return result.status == "authenticated" and bool(result.available_models)

    def supports_streaming(self) -> bool:
        return False

    def resolve_preferred_model(self, requested_model: str = "", available_models: list[str] | None = None) -> str:
        models = list(available_models or self._last_discovered_models or [])
        if not models:
            models, _ = self._discover_models()
        if not models:
            return ""

        exact_candidates = []
        normalized_requested = _normalize_requested_model(requested_model)
        candidate_order = []
        for candidate in [normalized_requested, TARGET_MODEL, DEFAULT_FALLBACK_MODEL]:
            if candidate and candidate not in candidate_order:
                candidate_order.append(candidate)
        for candidate in candidate_order:
            exact = _find_model_case_insensitive(models, candidate)
            if exact:
                exact_candidates.append(exact)
        if exact_candidates:
            return exact_candidates[0]

        ranked = sorted(models, key=_codex_model_rank, reverse=True)
        return ranked[0] if ranked else ""

    def _discover_models(self) -> tuple[list[str], str]:
        models = _list_models_from_openclaw_cli()
        source = "openclaw_cli"
        if not models:
            models = _list_models_from_config(self._config)
            source = "openclaw_config"
        self._last_discovered_models = models
        self._last_discovery_source = source
        return models, source

    def _probe_target_model(self) -> tuple[bool, str]:
        if not requests:
            return False, "requests_not_installed"
        if not self.gateway_ready:
            return False, "gateway_unavailable"
        cached_ok, cached_at, cached_reason = self._target_probe_cache
        if (time.time() - cached_at) < self._probe_ttl_seconds:
            return cached_ok, cached_reason

        payload = {
            "model": TARGET_MODEL,
            "messages": [{"role": "user", "content": "Reply exactly with OK"}],
            "max_tokens": 4,
            "temperature": 0,
            "stream": False,
        }
        try:
            resp = requests.post(
                f"{self.gateway_base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=min(self._timeout, 30),
            )
            if resp.status_code == 200:
                self._target_probe_cache = (True, time.time(), "runtime_probe_ok")
                return True, "runtime_probe_ok"
            reason = f"runtime_probe_http_{resp.status_code}"
            self._target_probe_cache = (False, time.time(), reason)
            return False, reason
        except requests.Timeout:
            self._target_probe_cache = (False, time.time(), "runtime_probe_timeout")
            return False, "runtime_probe_timeout"
        except Exception as exc:
            reason = f"runtime_probe_error:{exc}"
            self._target_probe_cache = (False, time.time(), reason)
            return False, reason

    def _should_try_target_model(self, requested_model: str) -> bool:
        normalized = _normalize_requested_model(requested_model)
        return normalized == TARGET_MODEL

    @staticmethod
    def _merge_models_with_target(models: list[str], target_model: str) -> list[str]:
        merged = list(models or [])
        if target_model and target_model not in merged:
            merged.append(target_model)
        return merged


def _load_json_file(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _resolve_gateway_http_base(cfg: dict) -> str:
    gateway = cfg.get("gateway") or {}
    remote = gateway.get("remote") or {}
    remote_url = str(remote.get("url") or "").strip()
    if remote_url.startswith("ws://"):
        return "http://" + remote_url[5:]
    if remote_url.startswith("wss://"):
        return "https://" + remote_url[6:]
    port = gateway.get("port") or 18789
    return f"http://127.0.0.1:{port}"


def _resolve_gateway_token(cfg: dict) -> str:
    gateway = cfg.get("gateway") or {}
    auth = gateway.get("auth") or {}
    return str(auth.get("token") or "").strip()


def _resolve_gateway_endpoint_enabled(cfg: dict, endpoint_name: str) -> bool:
    gateway = cfg.get("gateway") or {}
    http = gateway.get("http") or {}
    endpoints = http.get("endpoints") or {}
    endpoint = endpoints.get(endpoint_name) or {}
    return endpoint.get("enabled") is True


def _load_codex_profile(path: Path) -> tuple[str, dict]:
    data = _load_json_file(path)
    profiles = data.get("profiles") or {}
    last_good = data.get("lastGood") or {}
    preferred_id = str(last_good.get("openai-codex") or "openai-codex:default")
    profile = profiles.get(preferred_id) or profiles.get("openai-codex:default")
    if isinstance(profile, dict):
        return preferred_id, profile
    return "", {}


def _list_models_from_openclaw_cli() -> list[str]:
    command = _resolve_openclaw_cli_command()
    if not command:
        log.info("openclaw models list unavailable for codex discovery: cli_not_found")
        return []

    try:
        completed = subprocess.run(
            [*command, "models", "list", "--all", "--provider", "openai-codex", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        log.info(f"openclaw models list unavailable for codex discovery: {exc}")
        return []

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        log.info(f"openclaw models list failed for codex discovery: {stderr[:200]}")
        return []

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        log.info("openclaw models list returned invalid JSON for codex discovery")
        return []

    models = []
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if not key.startswith("openai-codex/"):
            continue
        if entry.get("missing") is True:
            continue
        if entry.get("available") is False:
            continue
        models.append(key)
    return list(dict.fromkeys(models))



def _resolve_openclaw_cli_command() -> list[str]:
    candidates = [
        shutil.which("openclaw.cmd"),
        shutil.which("openclaw"),
        str(OPENCLAW_NPM_DIR / "openclaw.cmd"),
        str(OPENCLAW_NPM_DIR / "openclaw"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return [str(path)]
    ps1_path = OPENCLAW_NPM_DIR / "openclaw.ps1"
    pwsh = shutil.which("pwsh") or shutil.which("pwsh.exe")
    if ps1_path.exists() and pwsh:
        return [pwsh, "-NoProfile", "-File", str(ps1_path)]
    return []

def _list_models_from_config(cfg: dict) -> list[str]:
    models = []
    defaults = (((cfg.get("agents") or {}).get("defaults") or {}).get("models") or {})
    if isinstance(defaults, dict):
        for key in defaults.keys():
            normalized = str(key).strip()
            if normalized.startswith("openai-codex/"):
                models.append(normalized)
    providers = (((cfg.get("models") or {}).get("providers") or {}).get("openai-codex") or {}).get("models") or []
    if isinstance(providers, list):
        for item in providers:
            if not isinstance(item, dict):
                continue
            normalized = str(item.get("id") or "").strip()
            if normalized.startswith("openai-codex/"):
                models.append(normalized)
    return list(dict.fromkeys(models))


def _normalize_requested_model(model_id: str) -> str:
    normalized = str(model_id or "").strip().lower()
    if not normalized:
        return TARGET_MODEL
    if normalized.startswith("openai-codex/"):
        return normalized
    if normalized.startswith("openai/"):
        suffix = normalized.split("/", 1)[1]
        if suffix.startswith("gpt-5"):
            return f"openai-codex/{suffix}"
    if normalized.startswith("gpt-5"):
        return f"openai-codex/{normalized}"
    return normalized


def _find_model_case_insensitive(models: list[str], target: str) -> str:
    needle = str(target or "").strip().lower()
    for model in models:
        if model.lower() == needle:
            return model
    return ""


def _codex_model_rank(model_id: str) -> tuple[int, int, int, int, str]:
    normalized = str(model_id or "").strip().lower()
    if normalized == TARGET_MODEL:
        return (999, 999, 999, 999, normalized)

    version = 0
    feature_bonus = 0
    penalty = 0
    if normalized.startswith("openai-codex/gpt-5."):
        suffix = normalized.split("gpt-5.", 1)[1]
        number = []
        for ch in suffix:
            if ch.isdigit():
                number.append(ch)
            else:
                break
        if number:
            version = int("".join(number))
    if "codex" in normalized:
        feature_bonus += 50
    if normalized.endswith("-max"):
        feature_bonus += 10
    if normalized.endswith("-mini"):
        penalty += 30
    if normalized.endswith("-spark"):
        penalty += 40
    return (version, feature_bonus, -penalty, len(normalized), normalized)
