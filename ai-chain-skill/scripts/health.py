#!/usr/bin/env python3
"""
AIchain Health — health.json writer + system diagnostics.
"""

import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from scripts.controller import safe_read_json, atomic_write, get_paths


def write_health(cfg: dict, controller_state: dict, extra: dict = None):
    """Write health.json for external monitoring."""
    paths = get_paths(cfg)
    health = {
        "status": "healthy" if controller_state.get("system") == "NORMAL" else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_check": True,
        "system_state": controller_state.get("system", "UNKNOWN"),
        "circuit_state": controller_state.get("circuit", "UNKNOWN"),
        "primary_model": controller_state.get("original_primary") or "see_config",
        "rescue_model": controller_state.get("rescue_model"),
        "godmode": bool(controller_state.get("godmode")),
        "error_count": len(controller_state.get("error_timestamps", [])),
        "escalated_at": controller_state.get("escalated_at"),
        "capabilities": Path.home().joinpath(".openclaw", "aichain", "discovered_capabilities.json").exists(),
        "platform": platform.system(),
        "python": platform.python_version(),
        "skill_version": cfg.get("version_compat", {}).get("skill_version", "4.0.0"),
    }
    if extra:
        health.update(extra)

    atomic_write(paths["health_file"], health)
    return health


def check_freshness(table: dict, max_age_hours: float) -> tuple[bool, float]:
    """Check if routing table is fresh enough. Returns (is_fresh, age_hours)."""
    synopsis = table.get("last_synopsis")
    if not synopsis:
        return False, -1
    try:
        dt = datetime.fromisoformat(synopsis.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return age_h <= max_age_hours, age_h
    except ValueError:
        return False, -1


def get_status_report(cfg: dict, controller_state: dict) -> str:
    """Generate a human-readable status report."""
    paths = get_paths(cfg)
    state = controller_state
    lines = [
        "",
        "  ┌─ AICHAIN STATUS ─────────────────────────────────",
        f"  │ System:      {state.get('system', 'UNKNOWN')}",
        f"  │ Circuit:     {state.get('circuit', 'UNKNOWN')}",
        f"  │ Errors:      {len(state.get('error_timestamps', []))}",
        f"  │ Capabilities:{' ACTIVE' if Path.home().joinpath('.openclaw', 'aichain', 'discovered_capabilities.json').exists() else ' NONE'}",
    ]

    if state.get("godmode"):
        gm = state["godmode"]
        lines.append(f"  │ God Mode:    ACTIVE → {gm.get('model', '?')}")
    else:
        lines.append(f"  │ God Mode:    OFF")

    if state.get("system") == "ESCALATED":
        lines.append(f"  │ Rescue:      {state.get('rescue_model', '?')}")
        lines.append(f"  │ Original:    {state.get('original_primary', '?')}")

    lines.extend([
        f"  │ Last Change: {state.get('last_transition', 'Never')}",
        "  └─────────────────────────────────────────────────",
        "",
    ])
    return "\n".join(lines)
