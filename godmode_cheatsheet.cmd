@echo off
cls
color 0A
echo.
echo  ============================================================
echo  ⛓  AIchain Sovereign Controller — God Mode CLI Cheat Sheet
echo  ============================================================
echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  COMMAND                     │  EFFECT                  │
echo  ├─────────────────────────────────────────────────────────┤
echo  │                                                         │
echo  │  !godmode [model]            │  Instant pin, no savings │
echo  │  !auto                       │  Return to AIchain $0    │
echo  │  !status                     │  Active model + rank     │
echo  │  !escalate [reason]          │  Heavy Hitter mode       │
echo  │  !revert                     │  Force revert to $0      │
echo  │  !watch                      │  Ghost Watcher daemon    │
echo  │  !sync                       │  Force re-sync now       │
echo  │  !pin [context]              │  Test specialist pin     │
echo  │                                                         │
echo  └─────────────────────────────────────────────────────────┘
echo.
echo  ── GOD MODE ──────────────────────────────────────────────
echo.
echo   Pin any model instantly (no cost-saving):
echo     python aichain_bridge.py --godmode openai/o3-pro
echo     python aichain_bridge.py --godmode google/gemini-2.5-pro
echo     python aichain_bridge.py --godmode anthropic/claude-sonnet-4
echo.
echo   Return to AIchain optimization:
echo     python aichain_bridge.py --auto
echo.
echo  ── STATUS ^& DIAGNOSTICS ──────────────────────────────────
echo.
echo   Full status (model, rank, savings, escalation):
echo     python aichain_bridge.py --status
echo.
echo   Force re-sync from routing table:
echo     python aichain_bridge.py --sync --url "YOUR_URL"
echo.
echo  ── SOLVE ^& REVERT ────────────────────────────────────────
echo.
echo   Manual escalate to Heavy Hitter:
echo     python aichain_bridge.py --escalate "429_rate_limit"
echo.
echo   Force instant revert to $0:
echo     python aichain_bridge.py --revert
echo.
echo  ── GHOST WATCHER ──────────────────────────────────────────
echo.
echo   Start continuous log monitor:
echo     python aichain_bridge.py --watch --url "YOUR_URL"
echo.
echo   Auto-triggers: 3 errors in 5 min = ESCALATE
echo   Auto-reverts:  Success detected   = REVERT to $0
echo   Smart pins:    Keyword detected    = Specialist model
echo.
echo  ── SPECIALIST PINS ────────────────────────────────────────
echo.
echo   Test specialist trigger:
echo     python aichain_bridge.py --test-pin "image_analysis"
echo     python aichain_bridge.py --test-pin "deep_web_search"
echo.
echo   Config file: %%USERPROFILE%%\.openclaw\aichain_bridge\specialist_pins.json
echo.
echo  ── BACKUP ^& RECOVERY ──────────────────────────────────────
echo.
echo   Restore from backup:
echo     python aichain_bridge.py --restore
echo.
echo   Demote a failing model (6h cooldown):
echo     python aichain_bridge.py --demote "model/id" --ttl 6h
echo.
echo  ── TASK SCHEDULER ─────────────────────────────────────────
echo.
echo   Import scheduled task (run as Admin):
echo     schtasks /Create /XML "aichain_task.xml" /TN "AIchain Ghost Watcher"
echo.
echo   Check task status:
echo     schtasks /Query /TN "AIchain Ghost Watcher" /FO LIST
echo.
echo   Delete task:
echo     schtasks /Delete /TN "AIchain Ghost Watcher" /F
echo.
echo  ── GITHUB ACTIONS ─────────────────────────────────────────
echo.
echo   Required Secrets (Settings ^> Secrets ^> Actions):
echo     OPENROUTER_KEY   = sk-or-v1-...
echo     GEMINI_KEY       = AIzaSy...
echo     GROQ_KEY         = gsk_...
echo.
echo   First manual trigger:
echo     Repo ^> Actions tab ^> "AIchain — Intelligence Arbitration Cycle"
echo     ^> "Run workflow" ^> Run
echo.
echo  ============================================================
echo   AIchain v4.0 — Maximum Intelligence at Zero Cost.
echo  ============================================================
echo.
pause
