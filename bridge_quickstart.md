# AIchain Bridge — Quick Start Guide

> Deploy the bridge on your Windows workstation in 3 minutes.

---

## Prerequisites

- **Python 3.10+** (already installed)
- **requests** library (`pip install requests`)
- **OpenClaw** installed at `C:\Users\<you>\.openclaw\`
- **AIchain** deployed to GitHub Pages (Phase 1)

---

## 1. Configure the Routing URL

Open `aichain_bridge.py` and update the `DEFAULT_ROUTING_URL` constant with your actual GitHub Pages URL:

```python
DEFAULT_ROUTING_URL = "https://<your-username>.github.io/AIchain/ai_routing_table.json"
```

Or pass it at runtime via `--url`:

```powershell
python aichain_bridge.py --sync --url "https://your-user.github.io/AIchain/ai_routing_table.json"
```

---

## 2. Manual Sync (First Run)

```powershell
cd "C:\Users\filok\OneDrive\Desktop\AI chain for Open Claw envirement"
python aichain_bridge.py --sync --url "YOUR_URL_HERE"
```

This will:
1. Fetch the global routing table from GitHub
2. Back up your current `openclaw.json`
3. Inject the optimal primary model + fallback chain
4. Write atomically (no corruption risk)

---

## 3. Check Status

```powershell
python aichain_bridge.py --status
```

Output shows: active primary model, fallback chain, demotions, last sync time, and available backups.

---

## 4. Demote a Failing Model

If a model returns 429/503/auth errors:

```powershell
# Demote for 6 hours (default)
python aichain_bridge.py --demote "openai/gpt-4.1" --reason "429_rate_limit"

# Demote for 30 minutes
python aichain_bridge.py --demote "google/gemini-2.5-pro" --ttl 30m --reason "503_overloaded"

# Demote for 1 day
python aichain_bridge.py --demote "openai/o3-pro" --ttl 1d --reason "auth_error"
```

The bridge immediately re-syncs and promotes the next best free model.

---

## 5. Restore from Backup

If something goes wrong:

```powershell
python aichain_bridge.py --restore
```

Restores from the most recent of 3 rolling backups.

---

## 6. Background Daemon (12h Auto-Sync)

### Option A: Run in Terminal

```powershell
python aichain_bridge.py --daemon --url "YOUR_URL_HERE"
```

### Option B: Silent Background (Stealth Mode)

Double-click `aichain_stealth.vbs` — launches the daemon with no visible console window.

### Option C: Windows Task Scheduler (Recommended)

1. Open **Task Scheduler** → Create Basic Task
2. Name: `AIchain Bridge Sync`
3. Trigger: **Daily**, repeat every **12 hours**
4. Action: **Start a program**
   - Program: `python`
   - Arguments: `"C:\Users\filok\OneDrive\Desktop\AI chain for Open Claw envirement\aichain_bridge.py" --sync --url "YOUR_URL_HERE"`
   - Start in: `C:\Users\filok\OneDrive\Desktop\AI chain for Open Claw envirement`
5. Check "Run whether user is logged on or not"

---

## Config Mapping Reference

| AIchain Field | OpenClaw Target |
|---|---|
| Rank #1 model | `agents.defaults.model.primary` |
| Ranks #2-6 | `agents.defaults.model.fallbacks[]` |
| All injected models | `agents.defaults.models{}` (whitelist) |

### Provider Routing

| AIchain Prefix | OpenClaw Route |
|---|---|
| `openai/*` | Direct (`openai` provider) |
| `google/*` | Direct (`google` provider) |
| `deepseek/*` | Direct (`deepseek` provider) |
| `anthropic/*` | Via OpenRouter (`openrouter/anthropic/*`) |
| `meta-llama/*` | Via OpenRouter (`openrouter/meta-llama/*`) |
| Everything else | Via OpenRouter (`openrouter/<model>`) |

---

## File Locations

| File | Purpose |
|---|---|
| `~/.openclaw/openclaw.json` | OpenClaw config (modified by bridge) |
| `~/.openclaw/aichain_bridge/bridge_state.json` | Sync state |
| `~/.openclaw/aichain_bridge/demotions.json` | Active model demotions |
| `~/.openclaw/aichain_bridge/bridge.log` | Full operation log |
| `~/.openclaw/aichain_bridge/backups/` | Rolling config backups |

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `[FATAL] 'requests' is not installed` | `pip install requests` |
| `Config not found` | Check `--config` path or set `OPENCLAW_CONFIG` |
| Fetch fails repeatedly | Check internet, verify GitHub Pages URL |
| Config corrupted | Run `--restore` to roll back |
| Model demoted too long | Check `demotions.json` or wait for TTL expiry |
