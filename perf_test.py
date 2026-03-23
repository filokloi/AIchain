import time
import json
import urllib.request
import urllib.error
from pathlib import Path

# Load token
token_path = Path.home() / ".openclaw" / "aichain" / ".auth_token"
TOKEN = token_path.read_text().strip()

URL = "http://127.0.0.1:8080/v1/chat/completions"

def run_test(name, payload):
    req = urllib.request.Request(URL, json.dumps(payload).encode("utf-8"))
    req.add_header("Content-Type", "application/json")
    req.add_header("X-AIchain-Token", TOKEN)
    
    start = time.time()
    try:
        with urllib.request.urlopen(req) as response:
            resp = json.loads(response.read().decode())
            dur = time.time() - start
            print(f"[{name}] {dur:.2f}s | Route: {resp.get('_aichaind', {}).get('routed_model')} | Exec: {resp.get('_aichaind', {}).get('exec_latency_ms', 0):.2f}ms")
    except urllib.error.HTTPError as e:
        dur = time.time() - start
        print(f"[{name}] HTTP Error {e.code}: {e.read().decode()} (in {dur:.2f}s)")
    except Exception as e:
        dur = time.time() - start
        print(f"[{name}] Error: {e} (in {dur:.2f}s)")

if __name__ == "__main__":
    print("AIchain Performance Diagnostic")
    print("-" * 50)
    
    # 1. Trivial Request
    run_test("Trivial", {
        "messages": [{"role": "user", "content": "Respond only with OK. No other words."}],
        "max_tokens": 5
    })
    
    # 2. Deep Reasoning
    run_test("Deep Reasoning", {
        "messages": [{"role": "user", "content": "Provide a detailed mathematical proof for why there are infinitely many primes. Explain step by step."}],
        "max_tokens": 500
    })
    
    # 3. JSON Enforcement
    run_test("Strict JSON", {
        "messages": [{"role": "user", "content": "Extract these entities into JSON: Alice is 30, Bob is 25"}],
        "response_format": {"type": "json_object"}
    })
    
    # 4. Manual Override
    run_test("Manual Override (Anthropic)", {
        "messages": [{"role": "user", "content": "Say hello."}],
        "aichain_override": "anthropic/claude-3-haiku-20240307"
    })
