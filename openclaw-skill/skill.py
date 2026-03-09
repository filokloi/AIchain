#!/usr/bin/env python3
"""
openclaw-skill/skill.py — Thin OpenClaw Skill Bridge

This is the thin bridge between OpenClaw and the aichaind sidecar.
All logic lives in aichaind. This file only:
  1. Reads the auth token
  2. Forwards requests to aichaind via HTTP
  3. Returns responses to OpenClaw

This replaces the old ai-chain-skill/aichain.py monolith.
"""

import json
import os
import sys
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


DEFAULT_SIDECAR_URL = "http://127.0.0.1:8080"
TOKEN_PATH = Path.home() / ".openclaw" / "aichain" / ".auth_token"


def read_auth_token() -> str:
    """Read the aichaind per-startup auth token."""
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    return ""


def forward_to_sidecar(endpoint: str, payload: dict, sidecar_url: str = DEFAULT_SIDECAR_URL) -> dict:
    """Forward a request to the aichaind sidecar."""
    token = read_auth_token()
    headers = {
        "Content-Type": "application/json",
        "X-AIchain-Token": token,
    }

    url = f"{sidecar_url}{endpoint}"
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        return {
            "status": resp.status_code,
            "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text},
        }
    except requests.ConnectionError:
        return {"status": 503, "body": {"error": "aichaind sidecar not running. Start with: python -m aichaind.main"}}
    except requests.Timeout:
        return {"status": 504, "body": {"error": "aichaind sidecar timeout"}}
    except Exception as e:
        return {"status": 500, "body": {"error": str(e)}}


def cmd_chat(args):
    """Send a chat message via the sidecar."""
    payload = {
        "messages": [{"role": "user", "content": args.message}],
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }
    result = forward_to_sidecar("/v1/chat/completions", payload)

    if result["status"] == 200:
        content = result["body"].get("choices", [{}])[0].get("message", {}).get("content", "")
        print(content)
    else:
        print(f"Error ({result['status']}): {json.dumps(result['body'], indent=2)}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    """Check sidecar status."""
    token = read_auth_token()
    if not token:
        print("⚠ No auth token found — aichaind may not be running")
        return

    try:
        resp = requests.get(f"{DEFAULT_SIDECAR_URL}/health",
                          headers={"X-AIchain-Token": token}, timeout=5)
        print(f"aichaind status: {resp.status_code}")
    except requests.ConnectionError:
        print("⚠ aichaind sidecar not running")
    except Exception as e:
        print(f"Error: {e}")


def cmd_start(args):
    """Start the aichaind sidecar daemon."""
    import subprocess
    config = args.config or "config/default.json"
    print(f"Starting aichaind with config: {config}")
    subprocess.Popen(
        [sys.executable, "-m", "aichaind.main", config],
        cwd=str(Path(__file__).resolve().parent.parent),
        creationflags=0x00000008 if os.name == "nt" else 0,  # DETACHED_PROCESS on Windows
    )
    print("aichaind started in background")


def main():
    parser = argparse.ArgumentParser(
        prog="aichain-skill",
        description="AIchain OpenClaw Skill — Thin Bridge to aichaind sidecar"
    )
    sub = parser.add_subparsers(dest="command")

    # chat
    chat_p = sub.add_parser("chat", help="Send a chat message")
    chat_p.add_argument("message", help="Message to send")
    chat_p.add_argument("--max-tokens", type=int, default=4096)
    chat_p.add_argument("--temperature", type=float, default=0.7)

    # status
    sub.add_parser("status", help="Check sidecar status")

    # start
    start_p = sub.add_parser("start", help="Start aichaind daemon")
    start_p.add_argument("--config", help="Config file path")

    args = parser.parse_args()
    if args.command == "chat":
        cmd_chat(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "start":
        cmd_start(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
