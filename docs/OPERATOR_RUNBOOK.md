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

## Operational Observability
- `/health`: Liveness probe. Returns JSON like `{"status": "ok", "system_state": "NORMAL"}`.
- `/status`: Detailed telemetry. Exposes active router rules, fast_brain/heavy_brain mappings, and circuit breaker health statistics.

## Troubleshooting

### "Unauthorized: Invalid token"
`aichaind` uses a per-process 256-bit token securely stored in `~/.openclaw/aichain/.auth_token`. When hitting `http://127.0.0.1:8080/v1/chat/completions` manually, ensure you pass the `X-Aichain-Token` header.

### Daemon fails to bind 8080
Another proxy or orphaned AIchain instance might be running. Run `netstat -ano | findstr 8080` (Windows) or `lsof -i :8080` (POSIX) to find the PID and kill it, then delete `~/.openclaw/aichain/aichaind.pid` if it corrupted.

### Cloud Fallback to Local
If OpenRouter/Gemini keys are completely stripped or exhausted, AIchain gracefully degrades to local providers like Ollama/LMStudio if they listen on default ports. To debug local capabilities, use `python tools/profile_local_runtime.py`.
