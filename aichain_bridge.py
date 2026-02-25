#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║  AIchain Bridge v4.0 — Sovereign Deployment & Ghost Watcher     ║
║  aichain_bridge.py                                                ║
║                                                                   ║
║  The "Ghost Watcher" — Permanent Background Intelligence:        ║
║    • Syncs to global AIchain routing table (12h cycle)            ║
║    • Specialist keyword pinning for task-optimized models        ║
║    • God Mode CLI (!godmode, !auto, !status)                     ║
║    • Solve & Revert — escalate on failure, revert on success     ║
║    • Low-CPU log tailing with smart pattern detection             ║
║    • Atomic writes, rolling backups, stealth operation            ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import copy
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("[FATAL] 'requests' is not installed. Run: pip install requests")
    sys.exit(1)


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

BANNER = r"""
   _    ___ ____ _           _         ____       _     _
  / \  |_ _/ ___| |__   __ _(_)_ __   | __ ) _ __(_) __| | __ _  ___
 / _ \  | | |   | '_ \ / _` | | '_ \  |  _ \| '__| |/ _` |/ _` |/ _ \
/ ___ \ | | |___| | | | (_| | | | | | | |_) | |  | | (_| | (_| |  __/
/_/   \_|___\____|_| |_|\__,_|_|_| |_| |____/|_|  |_|\__,_|\__, |\___|
          v4.0 — Sovereign Ghost Watcher                    |___/
"""

VERSION = "4.0.0"

DEFAULT_ROUTING_URL = "https://<your-username>.github.io/AIchain/ai_routing_table.json"

# OpenClaw paths
OPENCLAW_DIR = Path.home() / ".openclaw"
OPENCLAW_CONFIG = OPENCLAW_DIR / "openclaw.json"
GATEWAY_ERROR_LOG = OPENCLAW_DIR / "gateway_error.log"

# Bridge data directory
BRIDGE_DIR = OPENCLAW_DIR / "aichain_bridge"
BACKUPS_DIR = BRIDGE_DIR / "backups"
DEMOTIONS_FILE = BRIDGE_DIR / "demotions.json"
ESCALATION_FILE = BRIDGE_DIR / "escalation_state.json"
BRIDGE_STATE_FILE = BRIDGE_DIR / "bridge_state.json"
SPECIALIST_PINS_FILE = BRIDGE_DIR / "specialist_pins.json"
GODMODE_FILE = BRIDGE_DIR / "godmode_state.json"
LOG_FILE = BRIDGE_DIR / "bridge.log"

SYNC_INTERVAL = 12 * 60 * 60  # 12 hours
MAX_FALLBACKS = 5
MAX_BACKUPS = 3
ESCALATION_SAFETY_TTL_HOURS = 1.0
WATCH_POLL_INTERVAL = 3  # seconds — low CPU tail loop

# Error detection thresholds
ERROR_THRESHOLD = 3
ERROR_WINDOW_SECONDS = 300  # 5 minutes

# Network retry
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2

# Provider mapping
DIRECT_PROVIDER_MAP = {
    "openai/": "openai",
    "google/": "google",
    "deepseek/": "deepseek",
    "anthropic/": "openrouter",
    "meta-llama/": "openrouter",
    "mistralai/": "openrouter",
    "qwen/": "openrouter",
}

# ─────────────────────────────────────────────
# SPECIALIST PINS (Task-Optimized Routing)
# ─────────────────────────────────────────────
# Define keyword → model pins for specialized tasks.
# When a prompt or log context matches a keyword group,
# the bridge pins the optimized model automatically.

DEFAULT_SPECIALIST_PINS = {
    "vision": {
        "triggers": [
            "image_analysis", "facial_recognition", "ocr",
            "screenshot", "visual_analysis", "photo",
            "face_detect", "image_forensics", "slika", "slike"
        ],
        "model": "google/gemini-2.5-pro",
        "note": "Vision/multimodal specialist"
    },
    "deep_research": {
        "triggers": [
            "deep_web_search", "evidence_synthesis", "data_correlation",
            "intelligence_report", "target_analysis", "threat_assessment",
            "investigacija", "analitika", "istraga"
        ],
        "model": "openai/o3-pro",
        "note": "Max-intelligence deep reasoning"
    },
    "code_engineering": {
        "triggers": [
            "refactor", "system_architecture", "openclaw_script",
            "kodiranje", "programiranje", "debugg",
            "reverse_engineer", "exploit_analysis"
        ],
        "model": "openai/gpt-4.1",
        "note": "System engineering & code"
    },
    "document_analysis": {
        "triggers": [
            "pdf_analysis", "document_forensics", "extract_text",
            "contract_review", "legal_analysis", "dokument"
        ],
        "model": "anthropic/claude-sonnet-4",
        "note": "Document comprehension specialist"
    }
}


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def setup_logging(verbose: bool = False):
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    log_level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        "[%(asctime)s] [AIchain-Bridge] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(log_level)
    ch.setFormatter(fmt)

    logger = logging.getLogger("aichain_bridge")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger


log = logging.getLogger("aichain_bridge")


# ─────────────────────────────────────────────
# ATOMIC FILE OPS
# ─────────────────────────────────────────────

def atomic_write_json(path: Path, data: dict):
    """Atomic write via tempfile + os.replace (zero EBADF risk)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_backup(config_path: Path):
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(config_path, BACKUPS_DIR / f"openclaw.json.bak.{ts}")
    backups = sorted(BACKUPS_DIR.glob("openclaw.json.bak.*"), key=lambda p: p.stat().st_mtime)
    while len(backups) > MAX_BACKUPS:
        backups.pop(0).unlink()


def restore_backup(config_path: Path) -> bool:
    backups = sorted(BACKUPS_DIR.glob("openclaw.json.bak.*"), key=lambda p: p.stat().st_mtime)
    if not backups:
        log.error("No backups found.")
        return False
    latest = backups[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error(f"Backup corrupted: {e}")
        return False
    shutil.copy2(latest, config_path)
    log.info(f"Config restored from {latest.name}")
    return True


# ─────────────────────────────────────────────
# NETWORK
# ─────────────────────────────────────────────

def fetch_routing_table(url: str) -> dict | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"Fetching routing table (attempt {attempt}/{MAX_RETRIES})...")
            resp = requests.get(url, timeout=30, headers={
                "User-Agent": f"AIchain-Bridge/{VERSION}",
                "Accept": "application/json",
            })
            resp.raise_for_status()
            data = resp.json()
            if "routing_hierarchy" not in data:
                log.error("Invalid routing table: missing 'routing_hierarchy'")
                return None
            log.info(f"Routing table OK: {data.get('total_models_analyzed', '?')} models")
            return data
        except requests.RequestException as exc:
            wait = BASE_BACKOFF_SECONDS ** attempt
            log.warning(f"Fetch failed: {exc}. Retrying in {wait}s...")
            if attempt < MAX_RETRIES:
                time.sleep(wait)
    log.error("All fetch attempts exhausted.")
    return None


# ─────────────────────────────────────────────
# ESCALATION STATE
# ─────────────────────────────────────────────

def load_escalation() -> dict:
    if not ESCALATION_FILE.exists():
        return {"escalated": False}
    try:
        with open(ESCALATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"escalated": False}


def save_escalation(state: dict):
    atomic_write_json(ESCALATION_FILE, state)


def begin_escalation(rescue_model: str, original_primary: str, reason: str):
    state = {
        "escalated": True,
        "escalated_at": datetime.now(timezone.utc).isoformat(),
        "rescue_model": rescue_model,
        "original_primary": original_primary,
        "reason": reason,
        "safety_ttl_hours": ESCALATION_SAFETY_TTL_HOURS,
    }
    save_escalation(state)
    log.info(f"⚡ ESCALATION ACTIVATED: {reason}")
    log.info(f"  Rescue: {rescue_model}")
    log.info(f"  Original $0: {original_primary}")


def end_escalation(reason: str = "task_success"):
    state = load_escalation()
    state["escalated"] = False
    state["reverted_at"] = datetime.now(timezone.utc).isoformat()
    state["revert_reason"] = reason
    save_escalation(state)
    log.info(f"✓ ESCALATION CLEARED: {reason}")


# ─────────────────────────────────────────────
# DEMOTION ENGINE
# ─────────────────────────────────────────────

def load_demotions() -> dict:
    if not DEMOTIONS_FILE.exists():
        return {}
    try:
        with open(DEMOTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        now = datetime.now(timezone.utc)
        active = {k: v for k, v in data.items()
                  if datetime.fromisoformat(v["expires_at"]) > now}
        if len(active) != len(data):
            atomic_write_json(DEMOTIONS_FILE, active)
        return active
    except (json.JSONDecodeError, IOError):
        return {}


def demote_model(model_id: str, ttl_hours: float, reason: str):
    demotions = load_demotions()
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    demotions[model_id] = {
        "demoted_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires.isoformat(),
        "ttl_hours": ttl_hours, "reason": reason,
    }
    atomic_write_json(DEMOTIONS_FILE, demotions)
    log.info(f"Model DEMOTED: {model_id} | {reason} | TTL: {ttl_hours}h")


# ─────────────────────────────────────────────
# GOD MODE STATE
# ─────────────────────────────────────────────

def load_godmode() -> dict:
    if not GODMODE_FILE.exists():
        return {"active": False}
    try:
        with open(GODMODE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"active": False}


def save_godmode(state: dict):
    atomic_write_json(GODMODE_FILE, state)


def activate_godmode(model_id: str, config_path: Path) -> bool:
    """Instant manual pin — no cost-saving, no AIchain override."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error(f"Cannot read config: {e}")
        return False

    current_primary = config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
    oc_id = resolve_openclaw_model_id(model_id)

    model_section = config.get("agents", {}).get("defaults", {}).get("model", {})
    model_section["primary"] = oc_id

    # Ensure in whitelist
    whitelist = config.get("agents", {}).get("defaults", {}).get("models", {})
    if oc_id not in whitelist:
        whitelist[oc_id] = {}

    create_backup(config_path)
    atomic_write_json(config_path, config)

    save_godmode({
        "active": True,
        "model": oc_id,
        "original_primary": current_primary,
        "activated_at": datetime.now(timezone.utc).isoformat(),
    })

    log.info(f"⚡ GOD MODE ACTIVATED: {oc_id}")
    log.info(f"  Cost-saving: DISABLED | AIchain override: SUSPENDED")
    return True


def deactivate_godmode(config_path: Path, routing_url: str) -> bool:
    """Return to AIchain optimization."""
    gm = load_godmode()
    if not gm.get("active"):
        log.info("God Mode not active.")
        return False

    save_godmode({"active": False, "deactivated_at": datetime.now(timezone.utc).isoformat()})
    log.info("God Mode DEACTIVATED. Returning to AIchain optimization...")

    # Re-sync to restore $0 primary
    return cmd_sync(routing_url, config_path)


# ─────────────────────────────────────────────
# SPECIALIST PINS
# ─────────────────────────────────────────────

def load_specialist_pins() -> dict:
    if SPECIALIST_PINS_FILE.exists():
        try:
            with open(SPECIALIST_PINS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    # Initialize with defaults
    atomic_write_json(SPECIALIST_PINS_FILE, DEFAULT_SPECIALIST_PINS)
    return DEFAULT_SPECIALIST_PINS


def check_specialist_trigger(text: str) -> dict | None:
    """Check if text matches any specialist pin trigger. Returns pin config or None."""
    pins = load_specialist_pins()
    text_lower = text.lower()
    for name, pin in pins.items():
        for trigger in pin.get("triggers", []):
            if trigger.lower() in text_lower:
                log.info(f"Specialist trigger matched: '{trigger}' → {pin['model']} ({name})")
                return pin
    return None


# ─────────────────────────────────────────────
# CONFIG INJECTION
# ─────────────────────────────────────────────

def resolve_openclaw_model_id(aichain_model: str) -> str:
    for prefix, provider in DIRECT_PROVIDER_MAP.items():
        if aichain_model.startswith(prefix):
            if provider == "openrouter" and not aichain_model.startswith("openrouter/"):
                return f"openrouter/{aichain_model}"
            return aichain_model
    return f"openrouter/{aichain_model}"


def select_optimal_models(routing_table: dict, demotions: dict) -> list[dict]:
    return [e for e in routing_table.get("routing_hierarchy", [])
            if e.get("model") and e["model"] not in demotions]


def get_heavy_hitter(routing_table: dict) -> dict | None:
    hh = routing_table.get("heavy_hitter", {})
    model_id = hh.get("model")
    if model_id and model_id != "N/A":
        for entry in routing_table.get("routing_hierarchy", []):
            if entry["model"] == model_id:
                return entry
    hierarchy = routing_table.get("routing_hierarchy", [])
    return max(hierarchy, key=lambda e: e.get("metrics", {}).get("intelligence", 0)) if hierarchy else None


def get_best_free_primary(routing_table: dict, demotions: dict) -> dict | None:
    for entry in routing_table.get("routing_hierarchy", []):
        if entry.get("tier") in ("OAUTH_BRIDGE", "FREE_FRONTIER") and entry["model"] not in demotions:
            return entry
    return None


def inject_config(config: dict, primary_entry: dict, fallback_entries: list[dict]) -> dict:
    config = copy.deepcopy(config)
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    model_section = defaults.setdefault("model", {})
    whitelist = defaults.setdefault("models", {})

    existing_primary = model_section.get("primary", "")
    existing_fallbacks = model_section.get("fallbacks", [])

    new_primary = resolve_openclaw_model_id(primary_entry["model"])
    new_fallbacks = []
    seen = {new_primary}

    for entry in fallback_entries:
        oc_id = resolve_openclaw_model_id(entry["model"])
        if oc_id not in seen:
            new_fallbacks.append(oc_id)
            seen.add(oc_id)

    for fb in existing_fallbacks:
        if fb not in seen:
            new_fallbacks.append(fb)
            seen.add(fb)
    if existing_primary and existing_primary not in seen:
        new_fallbacks.append(existing_primary)

    model_section["primary"] = new_primary
    model_section["fallbacks"] = new_fallbacks

    for mid in [new_primary] + new_fallbacks:
        if mid not in whitelist:
            whitelist[mid] = {}

    log.info(f"Primary: {new_primary}")
    log.info(f"Fallbacks: {new_fallbacks[:5]}{'...' if len(new_fallbacks) > 5 else ''}")
    return config


# ─────────────────────────────────────────────
# BRIDGE STATE
# ─────────────────────────────────────────────

def load_bridge_state() -> dict:
    if not BRIDGE_STATE_FILE.exists():
        return {}
    try:
        with open(BRIDGE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_bridge_state(state: dict):
    atomic_write_json(BRIDGE_STATE_FILE, state)


# ─────────────────────────────────────────────
# CORE COMMANDS
# ─────────────────────────────────────────────

def cmd_sync(routing_url: str, config_path: Path, dry_run: bool = False):
    """Full sync: fetch → select $0 primary → inject → write."""
    # Skip if God Mode is active
    gm = load_godmode()
    if gm.get("active"):
        log.info(f"God Mode active ({gm.get('model')}). Sync SKIPPED.")
        log.info("Run --auto to deactivate God Mode and re-enable AIchain.")
        return True

    log.info("=" * 60)
    log.info("AIchain Sync — Sovereign $0 Priority Mode")
    log.info("=" * 60)

    routing_table = fetch_routing_table(routing_url)
    if not routing_table:
        log.error("Sync FAILED: routing table unavailable.")
        return False

    if not config_path.exists():
        log.error(f"Config not found: {config_path}")
        return False

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            current_config = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error(f"Config read failed: {e}")
        return False

    demotions = load_demotions()

    # Check escalation safety TTL
    esc = load_escalation()
    if esc.get("escalated"):
        try:
            dt = datetime.fromisoformat(esc.get("escalated_at", ""))
            if (datetime.now(timezone.utc) - dt).total_seconds() / 3600 > ESCALATION_SAFETY_TTL_HOURS:
                log.info("Safety TTL expired — auto-reverting.")
                end_escalation("safety_ttl_expired")
        except ValueError:
            pass

    primary = get_best_free_primary(routing_table, demotions)
    if not primary:
        optimal = select_optimal_models(routing_table, demotions)
        primary = optimal[0] if optimal else None
    if not primary:
        log.error("No models available.")
        return False

    optimal = select_optimal_models(routing_table, demotions)
    fallbacks = [e for e in optimal if e["model"] != primary["model"]][:MAX_FALLBACKS]

    hh = get_heavy_hitter(routing_table)
    if hh and hh["model"] != primary["model"]:
        hh_id = resolve_openclaw_model_id(hh["model"])
        if hh_id not in [resolve_openclaw_model_id(f["model"]) for f in fallbacks]:
            fallbacks.append(hh)

    log.info(f"$0 Primary: {primary['model']} (intel={primary['metrics']['intelligence']}, "
             f"cost=${primary['metrics']['cost']:.6f})")

    if dry_run:
        log.info("[DRY RUN] No changes written.")
        return True

    new_config = inject_config(current_config, primary, fallbacks)
    create_backup(config_path)

    try:
        atomic_write_json(config_path, new_config)
    except Exception as e:
        log.error(f"CRITICAL: {e}. Restoring backup...")
        restore_backup(config_path)
        return False

    state = load_bridge_state()
    state.update({
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "last_primary": resolve_openclaw_model_id(primary["model"]),
        "last_primary_cost": primary["metrics"]["cost"],
        "last_primary_tier": primary["tier"],
        "last_primary_intel": primary["metrics"]["intelligence"],
        "heavy_hitter": hh["model"] if hh else "N/A",
        "models_analyzed": routing_table.get("total_models_analyzed", 0),
        "routing_status": routing_table.get("system_status", "UNKNOWN"),
        "version": routing_table.get("version", "?"),
    })
    save_bridge_state(state)

    log.info("Sync COMPLETE — $0 model locked.")
    return True


def cmd_escalate(reason: str, routing_url: str, config_path: Path):
    """Deploy Heavy Hitter rescue model."""
    log.info(f"ESCALATION — reason: {reason}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        current_primary = config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
    except (json.JSONDecodeError, IOError):
        current_primary = ""

    routing_table = fetch_routing_table(routing_url)
    if not routing_table:
        log.error("Cannot escalate: no routing table.")
        return False

    hh = get_heavy_hitter(routing_table)
    if not hh:
        log.error("Cannot escalate: no Heavy Hitter.")
        return False

    hh_oc_id = resolve_openclaw_model_id(hh["model"])
    begin_escalation(hh_oc_id, current_primary, reason)

    demotions = load_demotions()
    optimal = select_optimal_models(routing_table, demotions)
    fallbacks = [e for e in optimal if e["model"] != hh["model"]][:MAX_FALLBACKS]

    new_config = inject_config(config, hh, fallbacks)
    create_backup(config_path)

    try:
        atomic_write_json(config_path, new_config)
    except Exception as e:
        log.error(f"Escalation write failed: {e}")
        return False

    log.info(f"Target model failed. Tactical swap to {hh['model']} "
             f"(Cost: ${hh['metrics']['cost']:.6f})")
    log.info(f"SOLVE & REVERT ARMED: auto-revert after success or {ESCALATION_SAFETY_TTL_HOURS}h.")
    return True


def cmd_revert(config_path: Path):
    """Immediately revert to $0 primary."""
    esc = load_escalation()
    original = esc.get("original_primary", "")
    if not original:
        log.info("No escalation to revert.")
        return False

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error(f"Cannot read config: {e}")
        return False

    model_section = config.get("agents", {}).get("defaults", {}).get("model", {})
    rescue = model_section.get("primary", "")
    model_section["primary"] = original

    fallbacks = model_section.get("fallbacks", [])
    if rescue and rescue not in fallbacks:
        fallbacks.insert(0, rescue)
    model_section["fallbacks"] = fallbacks

    create_backup(config_path)
    try:
        atomic_write_json(config_path, config)
    except Exception as e:
        log.error(f"Revert failed: {e}")
        return False

    end_escalation("solve_and_revert")
    log.info(f"SOLVE & REVERT COMPLETE → {original} | Cost: $0.00")
    return True


def cmd_watch(routing_url: str, config_path: Path):
    """
    Ghost Watcher — low-CPU continuous log monitor.
    Auto-escalate on errors, auto-revert on success, specialist pin detection.
    """
    log.info("=" * 60)
    log.info("AIchain Ghost Watcher — Active")
    log.info(f"Polling: {WATCH_POLL_INTERVAL}s | Error threshold: {ERROR_THRESHOLD}/{ERROR_WINDOW_SECONDS}s")
    log.info(f"Log target: {GATEWAY_ERROR_LOG}")
    log.info("Ctrl+C to stop.")
    log.info("=" * 60)

    ERROR_RE = re.compile(
        r"(429|rate.?limit|503|overloaded|502|bad.?gateway|"
        r"401|unauthorized|auth.?error|ECONNREFUSED|timeout|"
        r"loop.?detect|infinite.?loop|retry.?exhaust|"
        r"reasoning.?loop|provider.?error|model.?unavailable)", re.IGNORECASE
    )
    SUCCESS_RE = re.compile(
        r"(200\s+OK|response.?received|completion.?success|"
        r"stream.?complete|tokens.?generated|finish_reason|"
        r"\"done\":\s*true|\"status\":\s*\"ok\")", re.IGNORECASE
    )
    SPECIALIST_RE = re.compile(
        r"(image_analysis|facial_recognition|ocr|visual_analysis|"
        r"deep_web_search|evidence_synthesis|data_correlation|"
        r"intelligence_report|target_analysis|threat_assessment|"
        r"document_forensics|reverse_engineer|exploit_analysis)", re.IGNORECASE
    )

    error_timestamps = []
    last_pos = 0

    # Start from end of log (don't process history)
    if GATEWAY_ERROR_LOG.exists():
        last_pos = GATEWAY_ERROR_LOG.stat().st_size

    while True:
        try:
            esc = load_escalation()

            # ── Safety TTL check ──
            if esc.get("escalated"):
                try:
                    dt = datetime.fromisoformat(esc.get("escalated_at", ""))
                    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    if age_h > ESCALATION_SAFETY_TTL_HOURS:
                        log.info("Safety TTL expired → auto-revert.")
                        cmd_revert(config_path)
                except ValueError:
                    pass

            # ── Tail log file (low-CPU: read only new bytes) ──
            if GATEWAY_ERROR_LOG.exists():
                current_size = GATEWAY_ERROR_LOG.stat().st_size

                if current_size < last_pos:
                    last_pos = 0  # log rotated

                if current_size > last_pos:
                    with open(GATEWAY_ERROR_LOG, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_pos)
                        new_data = f.read()
                    last_pos = current_size

                    for line in new_data.splitlines():
                        line = line.strip()
                        if not line:
                            continue

                        # ── Error detection ──
                        if ERROR_RE.search(line):
                            now = time.time()
                            error_timestamps.append(now)
                            error_timestamps = [t for t in error_timestamps
                                                 if now - t < ERROR_WINDOW_SECONDS]

                            log.warning(f"Error: {line[:120]}")

                            if len(error_timestamps) >= ERROR_THRESHOLD and not esc.get("escalated"):
                                log.info(f"Threshold hit ({len(error_timestamps)} errors). ESCALATING!")
                                cmd_escalate("auto_watch_threshold", routing_url, config_path)
                                error_timestamps.clear()
                                esc = load_escalation()

                        # ── Success during escalation → IMMEDIATE REVERT ──
                        elif esc.get("escalated") and SUCCESS_RE.search(line):
                            log.info(f"SUCCESS during escalation: {line[:100]}")
                            log.info("Heavy Hitter solved it. IMMEDIATE REVERT!")
                            cmd_revert(config_path)
                            esc = load_escalation()

                        # ── Specialist trigger detection ──
                        elif SPECIALIST_RE.search(line):
                            pin = check_specialist_trigger(line)
                            if pin and not esc.get("escalated"):
                                gm = load_godmode()
                                if not gm.get("active"):
                                    log.info(f"Specialist pin: {pin['note']} → {pin['model']}")
                                    activate_godmode(pin["model"], config_path)
                                    # Auto-deactivate after 30 minutes
                                    save_godmode({
                                        "active": True,
                                        "model": resolve_openclaw_model_id(pin["model"]),
                                        "original_primary": load_bridge_state().get("last_primary", ""),
                                        "activated_at": datetime.now(timezone.utc).isoformat(),
                                        "auto_expire_minutes": 30,
                                        "reason": f"specialist_pin_{pin['note']}"
                                    })

            # ── God Mode auto-expiry ──
            gm = load_godmode()
            if gm.get("active") and gm.get("auto_expire_minutes"):
                try:
                    activated = datetime.fromisoformat(gm["activated_at"])
                    age_min = (datetime.now(timezone.utc) - activated).total_seconds() / 60
                    if age_min > gm["auto_expire_minutes"]:
                        log.info("Specialist pin expired → returning to AIchain optimization.")
                        deactivate_godmode(config_path, routing_url)
                except (ValueError, KeyError):
                    pass

            time.sleep(WATCH_POLL_INTERVAL)

        except KeyboardInterrupt:
            log.info("Ghost Watcher stopped.")
            break


def cmd_status(config_path: Path):
    """Comprehensive status display."""
    print(BANNER)
    print(f"  Config: {config_path}")
    print()

    # Bridge state
    state = load_bridge_state()
    if state:
        last_sync = state.get("last_sync", "Never")
        if last_sync != "Never":
            try:
                dt = datetime.fromisoformat(last_sync)
                age = datetime.now(timezone.utc) - dt
                age_str = f"{int(age.total_seconds() // 3600)}h {int((age.total_seconds() % 3600) // 60)}m ago"
                last_sync = f"{dt.strftime('%Y-%m-%d %H:%M UTC')} ({age_str})"
            except ValueError:
                pass

        cost = state.get("last_primary_cost", 0)
        cost_str = "FREE ($0)" if cost <= 0 else f"${cost:.6f}"
        intel = state.get("last_primary_intel", "?")

        print(f"  ┌─ BRIDGE STATUS ──────────────────────────────")
        print(f"  │ Last Sync:       {last_sync}")
        print(f"  │ Status:          {state.get('routing_status', 'UNKNOWN')}")
        print(f"  │ Models Ranked:   {state.get('models_analyzed', '?')}")
        print(f"  │ Primary Cost:    {cost_str}")
        print(f"  │ Primary Intel:   {intel}")
        print(f"  │ Heavy Hitter:    {state.get('heavy_hitter', '?')}")
        print(f"  │ Version:         {state.get('version', '?')}")
        print(f"  └─────────────────────────────────────────────")
    else:
        print("  [!] Never synced. Run: python aichain_bridge.py --sync")
    print()

    # God Mode
    gm = load_godmode()
    if gm.get("active"):
        print(f"  ┌─ ⚡ GOD MODE ACTIVE ─────────────────────────")
        print(f"  │ Model: {gm.get('model', '?')}")
        print(f"  │ AIchain Override: SUSPENDED")
        print(f"  │ Cost-Saving: DISABLED")
        if gm.get("auto_expire_minutes"):
            try:
                dt = datetime.fromisoformat(gm["activated_at"])
                remaining = gm["auto_expire_minutes"] - (datetime.now(timezone.utc) - dt).total_seconds() / 60
                print(f"  │ Auto-expire: {max(0, remaining):.0f}m remaining")
            except (ValueError, KeyError):
                pass
        print(f"  └─────────────────────────────────────────────")
        print()

    # Escalation
    esc = load_escalation()
    if esc.get("escalated"):
        print(f"  ┌─ ⚔ ESCALATION ACTIVE ────────────────────────")
        print(f"  │ Rescue:     {esc.get('rescue_model', '?')}")
        print(f"  │ Original:   {esc.get('original_primary', '?')}")
        print(f"  │ Reason:     {esc.get('reason', '?')}")
        try:
            dt = datetime.fromisoformat(esc.get("escalated_at", ""))
            remaining = max(0, ESCALATION_SAFETY_TTL_HOURS - (datetime.now(timezone.utc) - dt).total_seconds() / 3600)
            print(f"  │ Auto-revert: {remaining:.1f}h")
        except ValueError:
            pass
        print(f"  └─────────────────────────────────────────────")
        print()

    # Config
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            model = config.get("agents", {}).get("defaults", {}).get("model", {})
            print(f"  ┌─ LIVE CONFIG ─────────────────────────────────")
            print(f"  │ Primary:  {model.get('primary', 'NOT SET')}")
            print(f"  │ Fallbacks:")
            for i, fb in enumerate(model.get("fallbacks", [])[:6], 1):
                print(f"  │   {i}. {fb}")
            print(f"  └─────────────────────────────────────────────")
        except (json.JSONDecodeError, IOError):
            print("  [!] Config read failed.")
    print()

    # Demotions
    demotions = load_demotions()
    if demotions:
        print(f"  Active demotions: {len(demotions)}")
        for mid, info in demotions.items():
            try:
                remaining = max(0, (datetime.fromisoformat(info["expires_at"]) - datetime.now(timezone.utc)).total_seconds() / 3600)
                print(f"    ✗ {mid} ({info.get('reason','?')}, {remaining:.1f}h left)")
            except ValueError:
                pass

    backups = sorted(BACKUPS_DIR.glob("openclaw.json.bak.*"), key=lambda p: p.stat().st_mtime) if BACKUPS_DIR.exists() else []
    print(f"  Backups: {len(backups)}")

    # Estimated savings
    if state:
        syncs = 1
        if state.get("last_sync"):
            print(f"\n  💰 Estimated savings: every sync locks $0 model → ∞ savings vs paid API")


def cmd_daemon(routing_url: str, config_path: Path):
    log.info("AIchain daemon started (Sovereign Deployment mode).")
    cmd_sync(routing_url, config_path)
    while True:
        try:
            log.info(f"Next sync in {SYNC_INTERVAL // 3600}h.")
            time.sleep(SYNC_INTERVAL)
            cmd_sync(routing_url, config_path)
        except KeyboardInterrupt:
            log.info("Daemon stopped.")
            break


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def parse_ttl(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("h"): return float(s[:-1])
    if s.endswith("m"): return float(s[:-1]) / 60.0
    if s.endswith("d"): return float(s[:-1]) * 24.0
    return float(s)


def main():
    parser = argparse.ArgumentParser(
        description="AIchain Bridge v4.0 — Sovereign Ghost Watcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=BANNER
    )

    # Core commands
    parser.add_argument("--sync", action="store_true", help="Sync routing table → inject $0 primary")
    parser.add_argument("--status", action="store_true", help="Full status display")
    parser.add_argument("--escalate", metavar="REASON", nargs="?", const="manual", help="Deploy Heavy Hitter")
    parser.add_argument("--revert", action="store_true", help="Immediate revert to $0")
    parser.add_argument("--watch", action="store_true", help="Ghost Watcher — continuous log monitor")
    parser.add_argument("--daemon", action="store_true", help="12h sync daemon")

    # God Mode
    parser.add_argument("--godmode", metavar="MODEL", help="Instant model pin (no cost-saving)")
    parser.add_argument("--auto", action="store_true", help="Return to AIchain optimization")

    # Demotion
    parser.add_argument("--demote", metavar="MODEL", help="Demote model for TTL")
    parser.add_argument("--ttl", default="6h", help="TTL (default: 6h)")
    parser.add_argument("--reason", default="manual", help="Reason")

    # Specialist Pins
    parser.add_argument("--test-pin", metavar="CONTEXT", help="Test specialist pin trigger against a context string")

    # Recovery
    parser.add_argument("--restore", action="store_true", help="Restore from backup")

    # Options
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--url", default=DEFAULT_ROUTING_URL, help="Routing table URL")
    parser.add_argument("--config", default=str(OPENCLAW_CONFIG), help="Config path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    cp = Path(args.config)

    # Default to --status
    if not any([args.sync, args.status, args.escalate, args.revert, args.watch,
                args.daemon, args.godmode, args.auto, args.demote, args.test_pin, args.restore]):
        args.status = True

    if args.status:
        cmd_status(cp)
    elif args.sync:
        sys.exit(0 if cmd_sync(args.url, cp, args.dry_run) else 1)
    elif args.escalate:
        sys.exit(0 if cmd_escalate(args.escalate, args.url, cp) else 1)
    elif args.revert:
        sys.exit(0 if cmd_revert(cp) else 1)
    elif args.watch:
        cmd_watch(args.url, cp)
    elif args.daemon:
        cmd_daemon(args.url, cp)
    elif args.godmode:
        sys.exit(0 if activate_godmode(args.godmode, cp) else 1)
    elif args.auto:
        sys.exit(0 if deactivate_godmode(cp, args.url) else 1)
    elif args.demote:
        demote_model(args.demote, parse_ttl(args.ttl), args.reason)
        cmd_sync(args.url, cp)
    elif args.test_pin:
        pin = check_specialist_trigger(args.test_pin)
        if pin:
            print(f"Match: {pin['model']} — {pin['note']}")
        else:
            print("No specialist pin matched.")
    elif args.restore:
        if restore_backup(cp):
            end_escalation("manual_restore")
            save_godmode({"active": False})
        sys.exit(0)


if __name__ == "__main__":
    main()
