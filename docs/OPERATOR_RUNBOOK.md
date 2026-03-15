# AIchain Operator Runbook

AIchain is a local intelligence proxy and rule-based state machine that balances API cost against performance (fast "System 1" brains, heavy "System 2" reasoning models). It acts as a transparent router intercepting OpenAI-compatible requests and enforcing budgets, capabilities, and strict API access rules.

## Prerequisites
- **Python**: 3.11 or greater
- **Memory**: Minimal footprint (< 50MB RAM locally)
- **Ports**: 8080 must be available.

---

## Quickstart / Bootstrap
To deploy AIchain on a completely fresh machine, simply clone this repository and run the OS-specific setup script.

### Windows (PowerShell)
```powershell
# Installs dependencies, creates configs, checks port 8080.
.\setup.ps1
```

### Linux/macOS
```bash
./install.sh
```

---

## Operating Flow

### 1. Diagnostics (Doctor)
Before launching the service on a new node, run the included `aichain_doctor.py` to verify dependencies, permissions, and port bindings are pristine.
```bash
python tools/aichain_doctor.py
# Look for: "Doctor claims this node is READY"
```

### 2. Booting the Daemon
Do not invoke `python -m aichaind.main` manually without PYTHONPATH environment flags. Instead, use the launcher shims. Keep this process running in a background terminal.
- **Windows**: `.\start-aichain.ps1`
- **POSIX**: `./start-aichain.sh`

### 3. Verification (Smoke Test)
Run the native smoke test script while the daemon is actively listening.
```bash
python tools/aichain_smoke_test.py
```
This performs a safe local health lookup (`/health`), fetches operational telemetry (`/status`), and routes one trivial chat payload against the authenticated daemon.

---

## Operational Observability & Metrics

AIchain collects lightweight decision-grade telemetry to help you monitor performance without dragging down your system with a heavy database footprint. This telemetry is attached securely to the `/status` endpoint payload.

- `/health`: Liveness probe. Returns JSON like `{"status": "ok", "system_state": "NORMAL"}`.
- `/status`: Extended operational telemetry payload. Contains:
  - **Provider Health Limits**: Global circuit-breaker thresholds per backend.
  - **Operator Metrics**: Cumulative operational counters.

### Interpreting Operator Metrics
When invoking `/status`, the `operator_metrics` sub-dictionary will reveal exactly how AIchain is handling your traffic behind the gateway.

- **`total_requests`**: Absolute number of requests sent to the proxy bridge. If it’s stuck at zero, OpenClaw itself is failing to connect to the daemon HTTP port (8080).
- **`average_latency_ms`**: An Exponentially Weighted Moving Average (~10-request window) of provider execution time. If this spikes significantly, a provider is bogging down, consider using God Mode to shift primary models.
- **`fallback_events`**: Number of times the primary model failed/timed out, and the Cascade Router successfully rescued the request by silently trying the next model in the pricing tier hierarchy. Over 2% fallback rate implies backend provider hostility or exhausted quotas.
- **`routes_selected`**: Distribution of exact backend target models responding to your system. Useful for validating that the fast-brain is correctly routing the bulk of requests instead of invoking expensive models unnecessarily.

## Troubleshooting

### "Unauthorized: Invalid token"
`aichaind` uses a per-process 256-bit token securely stored in `~/.openclaw/aichain/.auth_token`. When hitting `http://127.0.0.1:8080/v1/chat/completions` manually, ensure you pass the `X-Aichain-Token` header.

### Daemon fails to bind 8080
Another proxy or orphaned AIchain instance might be running. Run `netstat -ano | findstr 8080` (Windows) or `lsof -i :8080` (POSIX) to find the PID and kill it, then delete `~/.openclaw/aichain/aichaind.pid` if it corrupted.

### Cloud Fallback to Local
If OpenRouter/Gemini keys are completely stripped or exhausted, AIchain gracefully degrades to local providers like Ollama/LMStudio if they listen on default ports. To debug local capabilities, use `python tools/profile_local_runtime.py`.
