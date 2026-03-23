import time
import json
import urllib.request
from pathlib import Path

token_path = Path.home() / ".openclaw" / "aichain" / ".auth_token"
TOKEN = token_path.read_text().strip()
URL = "http://127.0.0.1:8080/v1/chat/completions"

req = urllib.request.Request(URL, json.dumps({
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
}).encode("utf-8"))
req.add_header("Content-Type", "application/json")
req.add_header("X-AIchain-Token", TOKEN)

start = time.time()
with urllib.request.urlopen(req) as response:
    raw = response.read().decode()
    end = time.time()
    resp = json.loads(raw)
    telemetry = resp.get("_aichaind", {})
    exec_latency = telemetry.get("exec_latency_ms", 0) / 1000.0
    total_latency = end - start
    print(f"Total Client Latency: {total_latency:.3f}s")
    print(f"Server Exec Latency : {exec_latency:.3f}s")
    print(f"Unaccounted Overhead: {total_latency - exec_latency:.3f}s")
    print(json.dumps(telemetry, indent=2))
