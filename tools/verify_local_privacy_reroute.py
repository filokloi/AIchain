#!/usr/bin/env python3
"""Run a real privacy fail-closed -> local success scenario against temporary aichaind instances."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _QuietHandlerMixin:
    def log_message(self, format, *args):
        return


class ModelStubHandler(_QuietHandlerMixin, BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        payload = {
            "object": "list",
            "data": [
                {"id": "qwen-local-mock", "object": "model", "owned_by": "local"},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        payload = json.loads(raw or b"{}")
        model = payload.get("model", "qwen-local-mock")
        response = {
            "id": "chatcmpl-local-stub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "LOCAL_REROUTE_OK",
                    },
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StaticCatalogHandler(_QuietHandlerMixin, SimpleHTTPRequestHandler):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _wait_http_ok(url: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _wait_file(path: Path, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.25)
    return False


def _request_payload() -> dict:
    return {
        "messages": [
            {
                "role": "user",
                "content": "My email is alice@example.com and my SSN is 123-45-6789. Summarize my private note in one sentence.",
            }
        ],
        "max_tokens": 40,
    }


def _run_daemon_scenario(*, catalog_port: int, model_port: int, local_enabled: bool) -> dict:
    daemon_port = _free_port()
    tmp_root = Path(tempfile.mkdtemp(prefix="aichain-local-reroute-", dir=str(REPO_ROOT / "tmp" if (REPO_ROOT / "tmp").exists() else REPO_ROOT)))
    data_dir = tmp_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_root / "daemon.log"
    override_path = tmp_root / "config.override.json"
    token_path = data_dir / ".auth_token"

    override = {
        "routing_url": f"http://127.0.0.1:{catalog_port}/catalog_manifest.json",
        "data_dir": str(data_dir),
        "port": daemon_port,
        "local_execution": {
            "enabled": local_enabled,
            "provider": "local",
            "base_url": f"http://127.0.0.1:{model_port}/v1",
            "default_model": "local/qwen-local-mock",
            "require_healthcheck": True,
            "auto_detect": False,
            "preferred_providers": ["local"],
        },
    }
    override_path.write_text(json.dumps(override, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["AICHAIND_CONFIG_OVERRIDE"] = str(override_path)
    proc = None
    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                [sys.executable, "-m", "aichaind.main"],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

        if not _wait_file(token_path):
            raise RuntimeError("auth token was not created by temporary aichaind instance")
        if not _wait_http_ok(f"http://127.0.0.1:{daemon_port}/health"):
            raise RuntimeError("temporary aichaind instance did not become healthy")

        token = token_path.read_text(encoding="utf-8").strip()
        headers = {"X-AIchain-Token": token, "Content-Type": "application/json"}
        response = requests.post(
            f"http://127.0.0.1:{daemon_port}/v1/chat/completions",
            headers=headers,
            json=_request_payload(),
            timeout=60.0,
        )
        body = response.json()
        return {
            "http_status": response.status_code,
            "response_model": body.get("model", ""),
            "aichaind": body.get("_aichaind", {}),
            "error": body.get("error", ""),
            "log_path": str(log_path),
            "override_path": str(override_path),
            "local_enabled": local_enabled,
        }
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="aichain-local-reroute-suite-", dir=str(REPO_ROOT / "tmp" if (REPO_ROOT / "tmp").exists() else REPO_ROOT)))
    static_dir = root / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "catalog_manifest.json", static_dir / "catalog_manifest.json")

    catalog_port = _free_port()
    model_port = _free_port()
    catalog_server = ThreadingHTTPServer(("127.0.0.1", catalog_port), partial(StaticCatalogHandler, directory=str(static_dir)))
    model_server = ThreadingHTTPServer(("127.0.0.1", model_port), ModelStubHandler)
    _start_server(catalog_server)
    _start_server(model_server)

    try:
        baseline = _run_daemon_scenario(catalog_port=catalog_port, model_port=model_port, local_enabled=False)
        local_enabled = _run_daemon_scenario(catalog_port=catalog_port, model_port=model_port, local_enabled=True)

        baseline_blocked = baseline["http_status"] == 403 and "cloud_routing_blocked_by_policy" in baseline.get("error", "")
        enabled_local = local_enabled["http_status"] == 200 and (local_enabled.get("aichaind", {}).get("routed_provider") == "local")
        result = {
            "status": "runtime_confirmed" if baseline_blocked and enabled_local else "target_form_not_reached",
            "baseline": baseline,
            "local_enabled": local_enabled,
            "baseline_blocked": baseline_blocked,
            "local_execution_succeeded": enabled_local,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "runtime_confirmed" else 1
    finally:
        catalog_server.shutdown()
        catalog_server.server_close()
        model_server.shutdown()
        model_server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
