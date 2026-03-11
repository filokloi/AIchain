#!/usr/bin/env python3
"""Verify live task-fit routing against the local aichaind instance."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

CASES = [
    {
        "name": "chat",
        "prompt": "Say exactly CLOUD_OK and nothing else.",
        "max_tokens": 20,
        "expected_provider_class": "cloud",
    },
    {
        "name": "reasoning",
        "prompt": "Explain in 2 sentences why amortized analysis matters for dynamic arrays.",
        "max_tokens": 120,
        "expected_provider_class": "cloud",
    },
    {
        "name": "structured",
        "prompt": "Return only minified JSON with keys ok and answer where ok is true and answer is 7.",
        "max_tokens": 60,
        "expected_provider_class": "cloud",
    },
    {
        "name": "coding",
        "prompt": "Write only Python code for a function add(a, b) with a unit test.",
        "max_tokens": 120,
        "expected_provider_class": "cloud",
    },
]

LOCAL_PROVIDERS = {"local", "vllm", "ollama", "lmstudio", "llamacpp"}


@dataclass
class TaskFitCaseResult:
    name: str
    expected_provider_class: str
    http_status: int
    routed_model: str
    routed_provider: str
    provider_class: str
    route_layers: list[str]
    ok: bool
    error: str
    content_preview: str


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _expected_provider_class(case: dict, health: dict | None = None) -> str:
    if case.get("name") != "coding" or not isinstance(health, dict):
        return str(case.get("expected_provider_class") or "cloud")
    codex = ((health.get("provider_access") or {}).get("openai-codex") or {})
    if codex.get("runtime_confirmed") and codex.get("target_form_reached"):
        return "cloud"
    local_profile = (health.get("local_profiles") or {}).get("active_profile") or {}
    local_brain = str(health.get("local_brain") or "")
    coding_profile = (local_profile.get("task_profiles") or {}).get("coding") or {}
    coding_suitability = float(((local_profile.get("prompt_type_suitability") or {}).get("coding") or 0.0))
    if local_brain and local_profile.get("runtime_confirmed") and coding_profile.get("success") is True and coding_suitability >= 85.0:
        return "local"
    return "cloud"


def classify_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    return "local" if normalized in LOCAL_PROVIDERS else "cloud"


def verify_case(base_url: str, token: str, case: dict, health: dict | None = None) -> TaskFitCaseResult:
    headers = {
        "X-AIchain-Token": token,
        "Content-Type": "application/json",
    }
    payload = {
        "messages": [{"role": "user", "content": case["prompt"]}],
        "max_tokens": case["max_tokens"],
        "temperature": 0.0,
    }
    expected_provider_class = _expected_provider_class(case, health)
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,
        )
    except Exception as exc:
        return TaskFitCaseResult(
            name=case["name"],
            expected_provider_class=expected_provider_class,
            http_status=0,
            routed_model="",
            routed_provider="",
            provider_class="unknown",
            route_layers=[],
            ok=False,
            error=str(exc),
            content_preview="",
        )

    content_preview = ""
    try:
        body = response.json()
    except Exception:
        return TaskFitCaseResult(
            name=case["name"],
            expected_provider_class=expected_provider_class,
            http_status=response.status_code,
            routed_model="",
            routed_provider="",
            provider_class="unknown",
            route_layers=[],
            ok=False,
            error="invalid_json_response",
            content_preview=response.text[:200],
        )

    meta = body.get("_aichaind", {}) if isinstance(body, dict) else {}
    routed_provider = str(meta.get("routed_provider") or "")
    routed_model = str(meta.get("routed_model") or body.get("model") or "")
    provider_class = classify_provider(routed_provider)
    choices = body.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content_preview = str(message.get("content") or "")[:240]
    ok = response.status_code == 200 and provider_class == expected_provider_class
    return TaskFitCaseResult(
        name=case["name"],
        expected_provider_class=expected_provider_class,
        http_status=response.status_code,
        routed_model=routed_model,
        routed_provider=routed_provider,
        provider_class=provider_class,
        route_layers=list(meta.get("route_layers") or []),
        ok=ok,
        error=str(body.get("error") or "") if isinstance(body, dict) else "",
        content_preview=content_preview,
    )


def main() -> int:
    _configure_stdio()
    home = Path.home()
    token_path = home / '.openclaw' / 'aichain' / '.auth_token'
    token = token_path.read_text(encoding='utf-8').strip()
    base_url = 'http://127.0.0.1:8080'
    health = requests.get(f"{base_url}/health", headers={"X-AIchain-Token": token}, timeout=10).json()
    results = [verify_case(base_url, token, case, health) for case in CASES]
    payload = {
        'status': 'runtime_confirmed' if all(item.ok for item in results) else 'target_form_not_reached',
        'base_url': base_url,
        'results': [asdict(item) for item in results],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload['status'] == 'runtime_confirmed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
