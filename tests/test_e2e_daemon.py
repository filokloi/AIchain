#!/usr/bin/env python3
"""Pytest-friendly E2E smoke test for a running aichaind daemon."""

from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:8080"
TOKEN_PATH = Path.home() / ".openclaw" / "aichain" / ".auth_token"
AUDIT_PATH = Path.home() / ".openclaw" / "aichain" / "audit.jsonl"


def _daemon_available() -> bool:
    try:
        response = requests.get(f"{BASE}/health", timeout=1)
        return response.status_code == 200
    except Exception:
        return False


def _require_token() -> str:
    if not TOKEN_PATH.exists():
        pytest.skip("aichaind auth token not found")
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def _check(failures: list[str], name: str, passed: bool, info: str = ""):
    if not passed:
        failures.append(f"{name} failed {info}".strip())


@pytest.mark.skipif(not _daemon_available(), reason="aichaind daemon is not running on 127.0.0.1:8080")
def test_daemon_end_to_end():
    token = _require_token()
    headers = {"X-AIchain-Token": token, "Content-Type": "application/json"}
    failures: list[str] = []

    # 1. Health endpoint
    health = requests.get(f"{BASE}/health", timeout=5)
    _check(failures, "health 200", health.status_code == 200)
    payload = health.json()
    _check(failures, "has version", "version" in payload)
    _check(failures, "has system_state", "system_state" in payload)
    _check(failures, "auth active", payload.get("auth_active") is True)
    _check(failures, "has fast_brain", payload.get("fast_brain") != "")

    # 2. Auth rejection
    no_auth = requests.post(
        f"{BASE}/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        timeout=5,
    )
    _check(failures, "no auth -> 401", no_auth.status_code == 401, f"status={no_auth.status_code}")

    wrong_auth = requests.post(
        f"{BASE}/v1/chat/completions",
        headers={"X-AIchain-Token": "wrong"},
        json={"messages": [{"role": "user", "content": "hi"}]},
        timeout=5,
    )
    _check(failures, "wrong auth -> 401", wrong_auth.status_code == 401, f"status={wrong_auth.status_code}")

    # 3. PII redaction + privacy enforcement pipeline
    # Local privacy-safe fallback on a small LM Studio model can be materially slower than cloud routes.
    pii_resp = requests.post(
        f"{BASE}/v1/chat/completions",
        headers=headers,
        json={
            "messages": [{"role": "user", "content": "Email test@secret.com SSN 123-45-6789"}],
            "max_tokens": 50,
        },
        timeout=60,
    )
    pii_ok = pii_resp.status_code == 200
    pii_blocked = False
    if pii_resp.status_code == 403:
        try:
            pii_payload = pii_resp.json()
        except Exception:
            pii_payload = {}
        pii_blocked = str(pii_payload.get("error", "")).startswith("Policy:")
    _check(
        failures,
        "PII request handled by provider or policy firewall",
        pii_ok or pii_blocked,
        f"status={pii_resp.status_code}",
    )

    # 4. Layer 2/3 routing request
    # When coding-heavy routing correctly upgrades to OpenAI Codex OAuth, latency can be materially higher
    # than the old weak-local fallback. Keep this as a realistic smoke test instead of a brittle short timeout.
    code_resp = requests.post(
        f"{BASE}/v1/chat/completions",
        headers=headers,
        json={
            "messages": [{"role": "user", "content": "Write only Python code for a function add(a, b) with a unit test."}],
            "max_tokens": 120,
        },
        timeout=120,
    )
    _check(failures, "code request routed", code_resp.status_code == 200, f"status={code_resp.status_code}")
    if code_resp.status_code == 200:
        code_meta = code_resp.json().get("_aichaind", {})
        _check(failures, "code metadata exists", "routed_model" in code_meta)
        _check(failures, "code routed provider exists", "routed_provider" in code_meta)

    # 5. Simple chat should no longer 502 via OpenRouter fallback
    hello_resp = requests.post(
        f"{BASE}/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 50},
        timeout=60,
    )
    _check(failures, "hello routed", hello_resp.status_code == 200, f"status={hello_resp.status_code}")
    if hello_resp.status_code == 200:
        meta = hello_resp.json().get("_aichaind", {})
        _check(failures, "has metadata", "routed_model" in meta)
        _check(failures, "has routed_provider", "routed_provider" in meta)

    # 6. Wrong paths
    wrong_get = requests.get(f"{BASE}/wrong", timeout=5)
    _check(failures, "GET wrong -> 404", wrong_get.status_code == 404, f"status={wrong_get.status_code}")

    wrong_post = requests.post(f"{BASE}/v2/wrong", headers=headers, json={}, timeout=5)
    _check(failures, "POST wrong -> 404", wrong_post.status_code == 404, f"status={wrong_post.status_code}")

    # 7. Audit trail
    if AUDIT_PATH.exists():
        lines = [line for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        _check(failures, "audit entries exist", len(lines) >= 3, f"count={len(lines)}")
    else:
        failures.append("audit file missing")

    if failures:
        pytest.fail("\n".join(failures))


