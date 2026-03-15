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
DEFAULT_OPENCLAW_SESSION_ID = "openclaw-default"
TOKEN_PATH = Path.home() / ".openclaw" / "aichain" / ".auth_token"


def configure_stdio():
    """Prefer UTF-8 console output and degrade safely on Windows code pages."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


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


def build_chat_payload(args) -> dict:
    """Build sidecar payload for chat, including optional manual routing control."""
    payload = {
        "messages": [{"role": "user", "content": args.message}],
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }
    session_id = str(getattr(args, "session_id", "") or "").strip() or DEFAULT_OPENCLAW_SESSION_ID
    payload["session_id"] = session_id

    manual_model = str(getattr(args, "manual_model", "") or "").strip()
    manual_provider = str(getattr(args, "manual_provider", "") or "").strip()
    control = {}
    if getattr(args, "auto", False):
        control["mode"] = "auto"
    elif getattr(args, "manual", False) or manual_model or manual_provider:
        control["mode"] = "manual"
        if manual_model:
            control["model"] = manual_model
        if manual_provider:
            control["provider"] = manual_provider

    if control and getattr(args, "persist", False):
        control["persist_for_session"] = True
    if control:
        payload["_aichain_control"] = control
    return payload


def cmd_chat(args):
    """Send a chat message via the sidecar."""
    payload = build_chat_payload(args)
    result = forward_to_sidecar("/v1/chat/completions", payload)

    if result["status"] == 200:
        content = result["body"].get("choices", [{}])[0].get("message", {}).get("content", "")
        print(content)
    else:
        # Prevent raw JSON dumps to OpenClaw's stderr
        body = result["body"]
        error_msg = body.get("error", "") if isinstance(body, dict) else str(body)
        if result["status"] == 503 or result["status"] == 504:
            print(f"[AIchain] Daemon offline or unreachable ({result['status']}): {error_msg}", file=sys.stderr)
        else:
            print(f"[AIchain] Request failed ({result['status']}): {error_msg}", file=sys.stderr)
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
    configure_stdio()
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
    chat_p.add_argument("--session-id", default="", help="Reuse an existing AIchain session")
    chat_p.add_argument("--manual", action="store_true", help="Disable auto routing for this request and use the chosen model")
    chat_p.add_argument("--auto", action="store_true", help="Re-enable AIchain automatic routing")
    chat_p.add_argument("--manual-model", default="", help="Explicit model to lock, for example openai-codex/gpt-5.4")
    chat_p.add_argument("--manual-provider", default="", help="Explicit provider for manual mode")
    chat_p.add_argument("--persist", action="store_true", help="Persist manual/auto routing mode to the session")

    # status
    sub.add_parser("status", help="Check sidecar status")

    # start
    start_p = sub.add_parser("start", help="Start aichaind daemon")
    start_p.add_argument("--config", help="Config file path")

    args = parser.parse_args()
    if getattr(args, "manual", False) and getattr(args, "auto", False):
        parser.error("--manual and --auto cannot be used together")
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

