#!/usr/bin/env python3
"""
aichain_smoke_test.py - Live Verification Tool

Connects to a running aichaind instance on 127.0.0.1:8080 and verifies
the health, status, and correct evaluation of one routing scenario.
"""

import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

def get_auth_token() -> str:
    path = Path.home() / ".openclaw" / "aichain" / ".auth_token"
    if path.exists():
        return path.read_text().strip()
    return ""

def _fetch(url: str, post_data: dict = None, use_auth: bool = False):
    req = urllib.request.Request(url)
    if post_data:
        req.data = json.dumps(post_data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    if use_auth:
        token = get_auth_token()
        if token:
            req.add_header("X-AIchain-Token", token)
            req.add_header("Origin", "http://127.0.0.1:18789")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)

def smoke_test():
    print("AIchain Smoke Test -> Analyzing live daemon...")
    
    # 1. /health
    st, data = _fetch("http://127.0.0.1:8080/health")
    if st == 200 and isinstance(data, dict) and data.get("status") == "ok":
        print("[\033[32mPASS\033[0m] /health endpoint reached (" + data.get("version", "?") + ")")
    else:
        print("[\033[31mFAIL\033[0m] /health endpoint failed. Is aichaind running? " + str(data))
        return

    # 2. /status
    st, data = _fetch("http://127.0.0.1:8080/status")
    if st == 200 and isinstance(data, dict) and "fast_brain" in data.get("roles", {}):
        print("[\033[32mPASS\033[0m] /status endpoint successfully returned telemetry data.")
    else:
        print("[\033[31mFAIL\033[0m] /status endpoint unavailable or malformed. " + str(data))

    # 3. Route Evaluation
    print("-> Invoking simple route (requires auth)...")
    payload = {
        "model": "fast_brain",
        "messages": [{"role": "user", "content": "Trivial check: respond OK."}]
    }

    if not get_auth_token():
        print("[\033[33mWARN\033[0m] Cannot run routing test: .auth_token file missing.")
        return

    st, data = _fetch("http://127.0.0.1:8080/v1/chat/completions", post_data=payload, use_auth=True)
    if st == 200 and isinstance(data, dict):
        provider = data.get("model", "unknown")
        print("[\033[32mPASS\033[0m] Reached fast_brain natively! Fulfilled by: " + provider)
    else:
        print(f"[\033[31mFAIL\033[0m] Routing test failed ({st}): " + str(data))

if __name__ == "__main__":
    smoke_test()
