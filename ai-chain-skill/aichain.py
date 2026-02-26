#!/usr/bin/env python3
"""
AIchain v4.0 — Main Entry Point

Usage:
    python aichain.py --sync           Fetch ranking table + inject $0 primary
    python aichain.py --watch          Ghost Watcher (continuous log monitor)
    python aichain.py --status         Full status report
    python aichain.py --godmode MODEL  Instant model pin
    python aichain.py --auto           Return to AIchain control
    python aichain.py --escalate RSN   Escalate to Heavy Hitter
    python aichain.py --revert         Revert to $0 primary
    python aichain.py --restore        Restore from backup
    python aichain.py --test-pin CTX   Test specialist pin
    python aichain.py --daemon         12h sync loop
"""

import argparse
import logging
import sys
import time
import re
from pathlib import Path

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from scripts.controller import (
    Controller, load_config, get_paths, resolve_model_id,
    read_openclaw_config, inject_model, write_config,
    restore_latest_backup, safe_read_json, atomic_write,
    ERROR_PATTERNS, SUCCESS_PATTERNS
)
from scripts.sync import (
    fetch_routing_table, get_best_free_primary,
    get_heavy_hitter, get_top_fallbacks, compute_table_checksum
)
from scripts.discover import cmd_discover
from scripts.personalize import cmd_build_personal
from scripts.health import write_health, check_freshness, get_status_report

VERSION = "4.0.0"
BANNER = r"""
   _    ___ ____ _           _
  / \  |_ _/ ___| |__   __ _(_)_ __
 / _ \  | | |   | '_ \ / _` | | '_ \
/ ___ \ | | |___| | | | (_| | | | | |
/_/   \_|___\____|_| |_|\__,_|_|_| |_|  v4.0 — Sovereign Skill
"""


def setup_logging(paths: dict, verbose: bool = False):
    paths["data_dir"].mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "[%(asctime)s] [AIchain] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh = logging.FileHandler(paths["log_file"], encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)

    logger = logging.getLogger("aichain")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger


# ─────────────────────────────────────────
# SPECIALIST PINS
# ─────────────────────────────────────────

def check_specialist_pin(text: str, cfg: dict) -> dict | None:
    """Check specialist trigger keywords."""
    pins = cfg.get("specialist_pins", {})
    text_lower = text.lower()
    for name, pin in pins.items():
        for trigger in pin.get("triggers", []):
            if trigger.lower() in text_lower:
                return {"name": name, "model": pin["model"], "ttl": pin.get("ttl_minutes", 30)}
    return None


def filter_supported_entries(table: dict, oc_config: dict, log: logging.Logger) -> dict:
    """Keep only models that exist in OpenClaw allowed model list to avoid invalid model IDs."""
    allowed = set((oc_config.get("agents", {})
                   .get("defaults", {})
                   .get("models", {}) or {}).keys())

    # Safety net: include current primary/fallbacks from runtime config
    model_cfg = (oc_config.get("agents", {})
                 .get("defaults", {})
                 .get("model", {}) or {})
    if model_cfg.get("primary"):
        allowed.add(model_cfg["primary"])
    for fb in model_cfg.get("fallbacks", []) or []:
        allowed.add(fb)

    if not allowed:
        log.warning("No OpenClaw allowlist models found; skipping support filter")
        return table

    filtered = []
    for entry in table.get("routing_hierarchy", []):
        rid = resolve_model_id(entry.get("model", ""))

        # Guardrail: direct-provider "*:free" IDs frequently break OpenClaw runtime selection.
        if rid.startswith(("openai/", "google/", "deepseek/")) and ":free" in rid:
            continue

        if rid in allowed:
            filtered.append(entry)

    new_table = dict(table)
    new_table["routing_hierarchy"] = filtered
    log.info(f"Supported-model filter: kept {len(filtered)}/{len(table.get('routing_hierarchy', []))} entries")
    return new_table


# ─────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────

def cmd_sync(cfg: dict, ctrl: Controller, log: logging.Logger, dry_run: bool = False):
    """Full sync: fetch → select $0 → diff-based inject."""
    if ctrl.is_godmode:
        log.info(f"God Mode active. Sync skipped.")
        return True

    paths = get_paths(cfg)
    log.info("=" * 50)
    log.info("AIchain Sync — Sovereign Rank Mode")
    log.info("=" * 50)

    # Automatically try to build the personalized table if capabilities exist
    # If this fails (e.g. no capabilities discovered), fallback to standard fetch
    if not cmd_build_personal(cfg, log):
        table = fetch_routing_table(
            cfg["routing_url"], log, cfg.get("version_compat")
        )
    else:
        # Load the locally generated personalized table
        personalized_path = Path.home() / ".openclaw" / "aichain" / "personalized_routing_table.json"
        table = safe_read_json(personalized_path)

    if not table:
        return False

    oc_config = read_openclaw_config(paths["openclaw_config"])
    if not oc_config:
        log.error(f"Cannot read: {paths['openclaw_config']}")
        return False

    table = filter_supported_entries(table, oc_config, log)

    # Freshness check
    max_age = cfg.get("controller", {}).get("routing_table_max_age_hours", 48)
    is_fresh, age_h = check_freshness(table, max_age)
    if not is_fresh and age_h > 0:
        log.warning(f"Routing table is stale ({age_h:.1f}h > {max_age}h)")

    # Check TTL
    if ctrl.check_escalation_ttl():
        log.info("TTL expired — auto-reverting before sync")
        ctrl.complete_revert()

    primary = get_best_free_primary(table)
    if not primary:
        log.error("No $0 primary found")
        return False

    fallback_ids = get_top_fallbacks(table, primary["model"], cfg.get("max_fallbacks", 5))

    # Ensure Heavy Hitter in fallbacks
    hh = get_heavy_hitter(table)
    if hh and hh["model"] not in fallback_ids and hh["model"] != primary["model"]:
        fallback_ids.append(hh["model"])

    cost = primary["metrics"]["cost"]
    log.info(f"Primary: {primary['model']} (intel={primary['metrics']['intelligence']}, "
             f"cost={'$0' if cost <= 0 else f'${cost:.6f}'})")

    if dry_run:
        log.info("[DRY RUN] No changes written")
        return True

    new_config = inject_model(oc_config, primary["model"], fallback_ids)
    changed = write_config(
        paths["openclaw_config"], new_config,
        paths["backups_dir"], cfg.get("max_backups", 3), log
    )

    # Update health
    write_health(cfg, ctrl.state, {
        "last_sync": time.time(),
        "models_ranked": table.get("total_models_analyzed", 0),
        "heavy_hitter": hh["model"] if hh else "N/A",
        "table_checksum": compute_table_checksum(table),
    })

    log.info("Sync complete" + (" — config updated" if changed else " — no change needed"))
    return True


def cmd_escalate(cfg: dict, ctrl: Controller, log: logging.Logger, reason: str):
    """Deploy Heavy Hitter rescue model."""
    paths = get_paths(cfg)
    table = fetch_routing_table(cfg["routing_url"], log, cfg.get("version_compat"))
    if not table:
        return False

    oc_config = read_openclaw_config(paths["openclaw_config"])
    if not oc_config:
        log.error(f"Cannot read: {paths['openclaw_config']}")
        return False

    table = filter_supported_entries(table, oc_config, log)

    hh = get_heavy_hitter(table)
    if not hh:
        log.error("No Heavy Hitter available after support filtering")
        return False

    current_primary = oc_config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")

    hh_id = resolve_model_id(hh["model"])
    fallback_ids = get_top_fallbacks(table, hh["model"], cfg.get("max_fallbacks", 5))
    new_config = inject_model(oc_config, hh["model"], fallback_ids)

    write_config(paths["openclaw_config"], new_config, paths["backups_dir"], cfg.get("max_backups", 3), log)
    ctrl.begin_escalation(hh_id, current_primary, reason)

    log.info(f"ESCALATED → {hh['model']} (intel={hh['metrics']['intelligence']})")
    log.info(f"Auto-revert in {ctrl.escalation_ttl}m or on success")
    write_health(cfg, ctrl.state)
    return True


def cmd_revert(cfg: dict, ctrl: Controller, log: logging.Logger):
    """Revert to $0 primary."""
    paths = get_paths(cfg)
    original = ctrl.state.get("original_primary")
    if not original:
        log.info("Nothing to revert")
        return False

    oc_config = read_openclaw_config(paths["openclaw_config"])
    rescue = oc_config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")

    new_config = inject_model(oc_config, original, [rescue] if rescue else [])
    write_config(paths["openclaw_config"], new_config, paths["backups_dir"], cfg.get("max_backups", 3), log)
    ctrl.complete_revert()

    log.info(f"REVERTED → {original}")
    write_health(cfg, ctrl.state)
    return True


def cmd_godmode(cfg: dict, ctrl: Controller, log: logging.Logger, model: str):
    """Instant model pin."""
    paths = get_paths(cfg)
    oc_config = read_openclaw_config(paths["openclaw_config"])
    current = oc_config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")

    oc_id = resolve_model_id(model)
    new_config = inject_model(oc_config, model, [])
    write_config(paths["openclaw_config"], new_config, paths["backups_dir"], cfg.get("max_backups", 3), log)
    ctrl.set_godmode(oc_id, current)

    log.info(f"GOD MODE → {oc_id} | AIchain control: SUSPENDED")
    write_health(cfg, ctrl.state)
    return True


def cmd_auto(cfg: dict, ctrl: Controller, log: logging.Logger):
    """Return to AIchain control."""
    if not ctrl.is_godmode:
        log.info("God Mode not active")
        return False
    ctrl.clear_godmode()
    log.info("God Mode cleared — re-syncing...")
    return cmd_sync(cfg, ctrl, log)


def cmd_watch(cfg: dict, ctrl: Controller, log: logging.Logger):
    """Ghost Watcher — continuous log tailing with state machine transitions."""
    paths = get_paths(cfg)
    gateway_log = Path.home() / ".openclaw" / "gateway_error.log"

    log.info("=" * 50)
    log.info("AIchain Ghost Watcher — Active")
    log.info(f"Poll: {ctrl.poll_interval}s | Threshold: {ctrl.error_threshold}/{ctrl.error_window}s")
    log.info(f"Log: {gateway_log}")
    log.info("Ctrl+C to stop")
    log.info("=" * 50)

    # Specialist pin regex from config
    pin_triggers = []
    for name, pin in cfg.get("specialist_pins", {}).items():
        pin_triggers.extend(pin.get("triggers", []))
    SPECIALIST_RE = re.compile("|".join(re.escape(t) for t in pin_triggers), re.IGNORECASE) if pin_triggers else None

    last_pos = gateway_log.stat().st_size if gateway_log.exists() else 0
    last_sync_time = time.time()

    while True:
        try:
            # 4-Hour Periodic Sync
            now = time.time()
            if now - last_sync_time > 14400:
                log.info("Ghost Watcher: 4-hour cycle reached, executing periodic sync")
                cmd_sync(cfg, ctrl, log)
                last_sync_time = now

            # TTL check
            if ctrl.check_escalation_ttl():
                cmd_revert(cfg, ctrl, log)

            # Tail new log data
            if gateway_log.exists():
                size = gateway_log.stat().st_size
                if size < last_pos:
                    last_pos = 0  # log rotated
                if size > last_pos:
                    with open(gateway_log, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_pos)
                        new_data = f.read()
                    last_pos = size

                    for line in new_data.splitlines():
                        line = line.strip()
                        if not line:
                            continue

                        # Error detection
                        if ERROR_PATTERNS.search(line):
                            action = ctrl.record_error(line)
                            if action == "ESCALATE":
                                cmd_escalate(cfg, ctrl, log, "auto_threshold")

                        # Success during escalation
                        elif ctrl.state["system"] == "ESCALATED" and SUCCESS_PATTERNS.search(line):
                            action = ctrl.record_success()
                            if action == "REVERT":
                                log.info("Success during escalation — IMMEDIATE REVERT")
                                cmd_revert(cfg, ctrl, log)

                        # Specialist trigger
                        elif SPECIALIST_RE and SPECIALIST_RE.search(line):
                            if not ctrl.is_godmode and ctrl.state["system"] == "NORMAL":
                                pin = check_specialist_pin(line, cfg)
                                if pin:
                                    log.info(f"Specialist pin: {pin['name']} → {pin['model']}")
                                    cmd_godmode(cfg, ctrl, log, pin["model"])

            # Write health every cycle
            write_health(cfg, ctrl.state)
            time.sleep(ctrl.poll_interval)

        except KeyboardInterrupt:
            log.info("Ghost Watcher stopped")
            write_health(cfg, ctrl.state, {"status": "stopped"})
            break
        except Exception as e:
            log.error(f"Ghost Watcher crashed: {e}", exc_info=True)
            break


def cmd_status(cfg: dict, ctrl: Controller, log: logging.Logger):
    """Print full status."""
    print(BANNER)
    print(get_status_report(cfg, ctrl.state))

    paths = get_paths(cfg)
    oc_config = read_openclaw_config(paths["openclaw_config"])
    model = oc_config.get("agents", {}).get("defaults", {}).get("model", {})
    print(f"  ┌─ LIVE CONFIG ────────────────────────────────────")
    print(f"  │ Primary:  {model.get('primary', 'NOT SET')}")
    for i, fb in enumerate(model.get("fallbacks", [])[:5], 1):
        print(f"  │   {i}. {fb}")
    print(f"  └──────────────────────────────────────────────────")

    health = safe_read_json(paths["health_file"])
    if health:
        print(f"\n  Last sync models: {health.get('models_ranked', '?')}")
        print(f"  Heavy Hitter: {health.get('heavy_hitter', '?')}")


def cmd_daemon(cfg: dict, ctrl: Controller, log: logging.Logger):
    """12h sync loop."""
    interval = cfg.get("sync_interval_hours", 12) * 3600
    log.info(f"Daemon started — {interval // 3600}h cycle")
    cmd_sync(cfg, ctrl, log)
    while True:
        try:
            time.sleep(interval)
            cmd_sync(cfg, ctrl, log)
        except KeyboardInterrupt:
            log.info("Daemon stopped")
            break


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AIchain v4.0 — Sovereign AI Orchestration Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=BANNER
    )
    parser.add_argument("--sync", action="store_true", help="Sync $0 primary from ranking table")
    parser.add_argument("--watch", action="store_true", help="Ghost Watcher (continuous)")
    parser.add_argument("--status", action="store_true", help="Full status report")
    parser.add_argument("--godmode", metavar="MODEL", help="Instant model pin")
    parser.add_argument("--auto", action="store_true", help="Return to AIchain control")
    parser.add_argument("--escalate", metavar="REASON", nargs="?", const="manual", help="Deploy Heavy Hitter")
    parser.add_argument("--revert", action="store_true", help="Revert to $0")
    parser.add_argument("--restore", action="store_true", help="Restore from backup")
    parser.add_argument("--daemon", action="store_true", help="12h sync loop")
    parser.add_argument("--test-pin", metavar="CTX", help="Test specialist pin")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", default=str(Path(__file__).parent / "bridge_config.json"))
    parser.add_argument("--discover", action="store_true", help="Auto-discover hardware and API capabilities")
    parser.add_argument("--build-personal", action="store_true", help="Combine capabilities with global routing table")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    paths = get_paths(cfg)
    log = setup_logging(paths, args.verbose)
    ctrl = Controller(cfg, log)

    if not any([args.sync, args.watch, args.status, args.godmode, args.auto,
                args.escalate, args.revert, args.restore, args.daemon, args.test_pin,
                args.discover, args.build_personal]):
        args.status = True

    if args.status:
        cmd_status(cfg, ctrl, log)
    elif args.discover:
        cmd_discover(log)
    elif args.build_personal:
        cmd_build_personal(cfg, log)
    elif args.sync:
        sys.exit(0 if cmd_sync(cfg, ctrl, log, args.dry_run) else 1)
    elif args.watch:
        cmd_watch(cfg, ctrl, log)
    elif args.godmode:
        sys.exit(0 if cmd_godmode(cfg, ctrl, log, args.godmode) else 1)
    elif args.auto:
        sys.exit(0 if cmd_auto(cfg, ctrl, log) else 1)
    elif args.escalate:
        sys.exit(0 if cmd_escalate(cfg, ctrl, log, args.escalate) else 1)
    elif args.revert:
        sys.exit(0 if cmd_revert(cfg, ctrl, log) else 1)
    elif args.daemon:
        cmd_daemon(cfg, ctrl, log)
    elif args.test_pin:
        pin = check_specialist_pin(args.test_pin, cfg)
        if pin:
            print(f"Match: {pin['model']} ({pin['name']})")
        else:
            print("No match")
    elif args.restore:
        if restore_latest_backup(paths["openclaw_config"], paths["backups_dir"]):
            ctrl.complete_revert()
            log.info("Restored from backup")
        else:
            log.error("No backup available")


if __name__ == "__main__":
    main()
