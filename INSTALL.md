# AIchain — Skill Installation Guide

## Quick Install (End Users)

```powershell
# 1. Ensure OpenClaw is installed
openclaw --version

# 2. Install the AIchain skill from the GitHub repo
cd C:\Users\filok\.openclaw\workspace
git clone https://github.com/filokloi/AIchain.git
cd AIchain

# 3. Run the automated installer (as Administrator)
powershell -ExecutionPolicy Bypass -File .\ai-chain-skill\install.ps1

# 4. Restart OpenClaw gateway
openclaw gateway restart
```

That's it — the skill will automatically:
- Fetch the latest AI model rankings from `https://filokloi.github.io/AIchain/ai_routing_table.json`
- Select the best $0 model as your primary
- Configure fallback chain for reliability
- Set up 12-hour auto-sync via cron

## What It Does

- **Automatic Model Selection**: Analyzes 300+ models globally; picks strongest free model
- **Intelligent Failover**: 3 errors → escalate to Heavy Hitter (`openai/o3-pro`); success → auto-revert
- **Live Market Tracking**: Every 12h checks pricing/promo changes and re-ranks
- **Zero Cost**: All orchestration runs locally; no API keys needed for the controller

## Commands

```powershell
# Check status
python "C:\Users\filok\AppData\Roaming\npm\node_modules\openclaw\skills\ai-chain-skill\aichain.py" --status

# Force sync now
python "...\ai-chain-skill\aichain.py" --sync

# Temporarily pin a specific model
python "...\ai-chain-skill\aichain.py" --godmode "openai/gpt-4o"

# Return to AIchain control
python "...\ai-chain-skill\aichain.py" --auto

# View logs
Get-Content "C:\Users\filok\.openclaw\aichain\controller.log" -Tail 50 -Wait
```

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\filok\AppData\Roaming\npm\node_modules\openclaw\skills\ai-chain-skill\uninstall.ps1"
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Skill not visible in `openclaw status` | Re-run `install.ps1` as Administrator |
| Primary model not changing | Run `--sync` manually; check network access to GitHub Pages |
| Gateway errors after install | `openclaw gateway restart` |
| Cron not syncing | `cron list` to verify job `19e80cbf-...` is enabled |

## Technical Details

- **Skill location**: `%APPDATA%\npm\node_modules\openclaw\skills\ai-chain-skill`
- **Data dir**: `~/.openclaw/aichain`
- **Config**: `bridge_config.json` (sync interval, thresholds, specialist pins)
- **State**: `controller_state.json` (system state, circuit breaker, error history)
- **Cron job**: Every 12h, Europe/Belgrade timezone

## Governance

- Repo: https://github.com/filokloi/AIchain
- Branch workflow: `dev` → PR → `main` (protected)
- CI: `.github/workflows/validate.yml` (syntax checks, JSON validation)
- Policy: `MODEL_ROUTING_POLICY.md` ( AUTO mode, escalation, manual override rules)

---

*Last updated: 2026-02-26*
