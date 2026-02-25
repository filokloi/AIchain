---
name: AIchain
description: Sovereign AI model orchestration — automatic ranking, failover, and cost optimization for OpenClaw agents.
---

# AIchain Skill

Sovereign AI orchestration for OpenClaw. Pulls live intelligence rankings from
the public AIchain routing table and maintains the optimal $0 model as your
primary with deterministic failover to rescue models.

## Architecture

- **Brain A** (Controller) — Rule-based Python state machine. Zero cost,
  deterministic, fully testable. Handles all routing decisions.
- **Brain B** (Executor) — The highest-ranked AI model selected by the
  arbitrator. Dynamically replaceable via config injection.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File uninstall.ps1
```

## Commands

| Command | Effect |
|---------|--------|
| `python aichain.py --sync` | Fetch rankings, lock $0 primary |
| `python aichain.py --watch` | Continuous log monitor + auto-failover |
| `python aichain.py --status` | Full system status |
| `python aichain.py --godmode MODEL` | Instant model pin |
| `python aichain.py --auto` | Return to AIchain control |
| `python aichain.py --escalate REASON` | Deploy Heavy Hitter |
| `python aichain.py --revert` | Immediate revert to $0 |
| `python aichain.py --restore` | Restore config from backup |
| `python aichain.py --daemon` | 12h sync loop |
| `python aichain.py --test-pin CONTEXT` | Test specialist trigger |

## Requirements

- Python 3.11+
- `requests` library
- OpenClaw gateway running

## State Machine

```
NORMAL → DEGRADED → ESCALATED → RECOVERING → NORMAL
```

Error threshold, cooldown, and TTL are configurable in `bridge_config.json`.

## Files

| File | Purpose |
|------|---------|
| `aichain.py` | Main entry point |
| `bridge_config.json` | All configuration |
| `scripts/controller.py` | State machine + circuit breaker |
| `scripts/sync.py` | Routing table fetch + validation |
| `scripts/health.py` | Health output for monitoring |
| `install.ps1` | Automated installer |
| `uninstall.ps1` | Clean removal |
