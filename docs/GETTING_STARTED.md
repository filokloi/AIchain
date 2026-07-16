# Getting Started

Three ways in, sorted by how much you want to touch a terminal. Every path
ends the same: a local router at `http://127.0.0.1:8080/v1` that any
OpenAI-compatible app can use with model `aichain/auto`.

## Path 0 — nothing to install

- **Browse the catalog:** https://filokloi.github.io/AIchain/ — live ranking
  of 300+ models (intelligence, price, speed, free paths), rebuilt every 12h.
- **Chat with any model:** https://filokloi.github.io/ai-chain/ — sign in
  with OpenRouter (one click), streaming answers, automatic failover, and a
  badge under every answer showing which model replied and what it cost.

## Path 1 — one-line install (recommended)

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/filokloi/AIchain/main/scripts/get-aichain.ps1 | iex
```
Creates `%USERPROFILE%\AIchain`, installs dependencies, puts an
**"AIchain Router"** launcher on your Desktop. Double-click it — the router
prints the Base URL, API key and model to paste into any app.

**Linux / macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/filokloi/AIchain/main/scripts/get-aichain.sh | bash
aichain-router
```

## Path 2 — standalone binary (no Python at all)

Download `aichaind-windows.exe` or `aichaind-linux` from
[Releases](https://github.com/filokloi/AIchain/releases/latest) and run it.
That's the whole install. (Windows SmartScreen may warn on first run —
"More info → Run anyway"; binaries are built publicly by
[release.yml](../.github/workflows/release.yml).)

## Path 3 — developers

```bash
git clone https://github.com/filokloi/AIchain && cd AIchain
pip install -e .          # registers the `aichaind` command
aichaind                  # boots the sidecar on 127.0.0.1:8080
python -m pytest tests -q # full suite
```

**Docker** (self-hosting):
```bash
docker build -t aichaind .
docker run -p 8080:8080 -e OPENROUTER_KEY=sk-or-... -v aichain-data:/data aichaind
```

## Connect any app (all paths)

On boot the router prints a connect box. The values are always:

| Field | Value |
|---|---|
| Base URL | `http://127.0.0.1:8080/v1` |
| API key | contents of `~/.openclaw/aichain/.auth_token` |
| Model | `aichain/auto` · `aichain/economy` · `aichain/power` · `aichain/local` · `aichain/lock:<id>` |

## Give it your keys and rules (optional, powerful)

- Cloud models: set `OPENROUTER_KEY` (or provider keys) in your environment.
- Personal routing rules — budgets, free quotas, privacy boundaries — live in
  `~/.openclaw/aichain/user_truth.json`
  ([schema](../schemas/user_truth.schema.json)). Example: a daily hard-stop
  budget and a rule that anything mentioning a client never leaves your
  machine. This file is never uploaded anywhere.

## Verify it works

```bash
curl http://127.0.0.1:8080/v1/models        # lists aichain/* virtual models
curl http://127.0.0.1:8080/health           # {"status": "ok", ...}
```
Every chat answer includes an `_aichaind` block: routed model, effective
cost, failover chain — the router shows its work.
