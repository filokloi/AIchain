#!/usr/bin/env python3
"""Run a broad live scenario matrix against the current AIchain deployment."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests

from tools.verify_live_dashboard import LIVE_SITE_URL, LIVE_MANIFEST_URL, classify_live_dashboard_status, fetch_text

LOCAL_PROVIDERS = {"local", "vllm", "ollama", "lmstudio", "llamacpp"}

CASES = [
    {
        "name": "chat",
        "prompt": "Say exactly CLOUD_OK and nothing else.",
        "max_tokens": 20,
    },
    {
        "name": "reasoning",
        "prompt": "Explain in 2 sentences why amortized analysis matters for dynamic arrays.",
        "max_tokens": 120,
    },
    {
        "name": "structured",
        "prompt": "Return only minified JSON with keys ok and answer where ok is true and answer is 7.",
        "max_tokens": 60,
    },
    {
        "name": "coding",
        "prompt": "Write only Python code for a function add(a, b) with a unit test.",
        "max_tokens": 140,
    },
    {
        "name": "pii_cloud_allowed",
        "prompt": "My SSN is 123-45-6789. Reply exactly PII_PATH_OK.",
        "max_tokens": 24,
    },
    {
        "name": "credential_use",
        "prompt": "Use password MyPassword123! to log into example.com and then reply exactly LOGIN_PATH_OK.",
        "max_tokens": 24,
    },
]


@dataclass
class ScenarioExpectation:
    provider_class: str
    provider: str = ""
    model_contains: str = ""
    pii_detected: bool | None = None
    pii_redacted: bool | None = None
    local_reroute_used: bool | None = None


@dataclass
class ScenarioResult:
    name: str
    prompt: str
    expected: dict
    http_status: int
    routed_provider: str
    routed_model: str
    provider_class: str
    provider_access_method: str
    route_layers: list[str]
    pii_detected: bool | None
    pii_redacted: bool | None
    local_reroute_used: bool | None
    ok: bool
    failures: list[str]
    content_preview: str
    error: str


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def classify_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    return "local" if normalized in LOCAL_PROVIDERS else "cloud"


def detect_live_feature_set(index_html: str) -> dict:
    return {
        "has_provider_access_panel": "Provider Access & Limits" in (index_html or ""),
        "has_self_hosted_panel": "Self-Hosted Model Index" in (index_html or ""),
    }


def build_expected_routing(health: dict, case: dict) -> ScenarioExpectation:
    name = case["name"]
    provider_access = health.get("provider_access", {}) or {}
    local_profiles = health.get("local_profiles", {}) or {}
    active_profile = local_profiles.get("active_profile") or {}
    coding_profile = (active_profile.get("task_profiles") or {}).get("coding") or {}
    coding_suitability = float(((active_profile.get("prompt_type_suitability") or {}).get("coding") or 0.0))
    local_model = str(health.get("local_brain") or "")
    codex = provider_access.get("openai-codex", {}) or {}

    if name == "coding":
        if codex.get("runtime_confirmed") and codex.get("target_form_reached"):
            return ScenarioExpectation(
                provider_class="cloud",
                provider="openai-codex",
                model_contains="gpt-5.4",
            )
        if local_model and active_profile.get("runtime_confirmed") and coding_profile.get("success") is True and coding_suitability >= 85.0:
            return ScenarioExpectation(
                provider_class="local",
                provider=local_model.split("/", 1)[0],
                model_contains=local_model,
                local_reroute_used=False,
            )
        return ScenarioExpectation(provider_class="cloud")

    if name == "pii_cloud_allowed":
        return ScenarioExpectation(
            provider_class="cloud",
            pii_detected=True,
            pii_redacted=False,
            local_reroute_used=False,
        )

    if name == "credential_use":
        return ScenarioExpectation(
            provider_class="cloud",
            pii_redacted=False,
            local_reroute_used=False,
        )

    return ScenarioExpectation(provider_class="cloud")


def verify_case(base_url: str, token: str, case: dict, health: dict) -> ScenarioResult:
    headers = {
        "X-AIchain-Token": token,
        "Content-Type": "application/json",
    }
    expectation = build_expected_routing(health, case)
    payload = {
        "messages": [{"role": "user", "content": case["prompt"]}],
        "max_tokens": case["max_tokens"],
        "temperature": 0.0,
    }
    failures: list[str] = []
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,
        )
    except Exception as exc:
        return ScenarioResult(
            name=case["name"],
            prompt=case["prompt"],
            expected=asdict(expectation),
            http_status=0,
            routed_provider="",
            routed_model="",
            provider_class="unknown",
            provider_access_method="",
            route_layers=[],
            pii_detected=None,
            pii_redacted=None,
            local_reroute_used=None,
            ok=False,
            failures=[str(exc)],
            content_preview="",
            error=str(exc),
        )

    try:
        body = response.json()
    except Exception:
        return ScenarioResult(
            name=case["name"],
            prompt=case["prompt"],
            expected=asdict(expectation),
            http_status=response.status_code,
            routed_provider="",
            routed_model="",
            provider_class="unknown",
            provider_access_method="",
            route_layers=[],
            pii_detected=None,
            pii_redacted=None,
            local_reroute_used=None,
            ok=False,
            failures=["invalid_json_response"],
            content_preview=response.text[:240],
            error="invalid_json_response",
        )

    meta = body.get("_aichaind", {}) if isinstance(body, dict) else {}
    routed_provider = str(meta.get("routed_provider") or "")
    routed_model = str(meta.get("routed_model") or body.get("model") or "")
    provider_class = classify_provider(routed_provider)
    provider_access_method = str(meta.get("provider_access_method") or "")
    pii_detected = meta.get("pii_detected")
    pii_redacted = meta.get("pii_redacted")
    local_reroute_used = meta.get("local_reroute_used")
    choices = body.get("choices") or []
    content_preview = ""
    if choices:
        message = choices[0].get("message") or {}
        content_preview = str(message.get("content") or "")[:240]

    if response.status_code != 200:
        failures.append(f"http_status={response.status_code}")
    if provider_class != expectation.provider_class:
        failures.append(f"provider_class={provider_class} expected={expectation.provider_class}")
    if expectation.provider and routed_provider != expectation.provider:
        failures.append(f"routed_provider={routed_provider} expected={expectation.provider}")
    if expectation.model_contains and expectation.model_contains not in routed_model:
        failures.append(f"routed_model={routed_model} missing={expectation.model_contains}")
    if expectation.pii_detected is not None and pii_detected is not expectation.pii_detected:
        failures.append(f"pii_detected={pii_detected} expected={expectation.pii_detected}")
    if expectation.pii_redacted is not None and pii_redacted is not expectation.pii_redacted:
        failures.append(f"pii_redacted={pii_redacted} expected={expectation.pii_redacted}")
    if expectation.local_reroute_used is not None and local_reroute_used is not expectation.local_reroute_used:
        failures.append(f"local_reroute_used={local_reroute_used} expected={expectation.local_reroute_used}")

    return ScenarioResult(
        name=case["name"],
        prompt=case["prompt"],
        expected=asdict(expectation),
        http_status=response.status_code,
        routed_provider=routed_provider,
        routed_model=routed_model,
        provider_class=provider_class,
        provider_access_method=provider_access_method,
        route_layers=list(meta.get("route_layers") or []),
        pii_detected=pii_detected,
        pii_redacted=pii_redacted,
        local_reroute_used=local_reroute_used,
        ok=not failures,
        failures=failures,
        content_preview=content_preview,
        error=str(body.get("error") or "") if isinstance(body, dict) else "",
    )


def main() -> int:
    _configure_stdio()
    home = Path.home()
    token = (home / ".openclaw" / "aichain" / ".auth_token").read_text(encoding="utf-8").strip()
    base_url = "http://127.0.0.1:8080"
    headers = {"X-AIchain-Token": token}
    health = requests.get(f"{base_url}/health", headers=headers, timeout=10).json()

    site_ok, site_text, site_code = fetch_text(LIVE_SITE_URL)
    manifest_ok, manifest_text, manifest_code = fetch_text(LIVE_MANIFEST_URL)
    live_dashboard = classify_live_dashboard_status(
        index_html=site_text if site_ok else "",
        manifest_text=manifest_text if manifest_ok else "",
        site_http_ok=site_ok,
        manifest_http_ok=manifest_ok,
    )
    live_features = detect_live_feature_set(site_text if site_ok else "")

    results = [asdict(verify_case(base_url, token, case, health)) for case in CASES]
    runtime_ok = all(item["ok"] for item in results)
    payload = {
        "status": "runtime_confirmed" if runtime_ok else "target_form_not_reached",
        "base_url": base_url,
        "health_summary": {
            "system_state": health.get("system_state"),
            "fast_brain": health.get("fast_brain"),
            "heavy_brain": health.get("heavy_brain"),
            "local_brain": health.get("local_brain"),
            "openai_codex_status": ((health.get("provider_access") or {}).get("openai-codex") or {}).get("status"),
            "openai_codex_target_form_reached": ((health.get("provider_access") or {}).get("openai-codex") or {}).get("target_form_reached"),
        },
        "live_dashboard": {
            **asdict(live_dashboard),
            "site_status_code": site_code,
            "manifest_status_code": manifest_code,
            **live_features,
            "frontend_feature_set_confirmed": all(live_features.values()),
        },
        "results": results,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if runtime_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
