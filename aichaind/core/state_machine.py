#!/usr/bin/env python3
"""
aichaind.core.state_machine — Deterministic State Machine Controller

Migrated from ai-chain-skill/scripts/controller.py.
Rule-based control plane (Brain A). Zero-cost, fully testable,
no AI inference. Manages model selection via state transitions.

States:
    NORMAL     → System healthy, $0 primary active
    DEGRADED   → Errors detected, monitoring threshold
    ESCALATED  → Heavy Hitter deployed, waiting for success
    RECOVERING → Success detected, reverting to $0

Circuit Breaker:
    CLOSED     → Normal operation, requests pass through
    OPEN       → Failures exceeded threshold, failover active
    HALF_OPEN  → Cooldown expired, testing primary again
"""

import json
import hashlib
import os
import copy
import shutil
import tempfile
import logging
import time
import re
from enum import Enum
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ─────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────

class SystemState(str, Enum):
    NORMAL     = "NORMAL"
    DEGRADED   = "DEGRADED"
    ESCALATED  = "ESCALATED"
    RECOVERING = "RECOVERING"


class CircuitState(str, Enum):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# ─────────────────────────────────────────
# CONFIG LOADER
# ─────────────────────────────────────────

def resolve_path(p: str) -> Path:
    """Resolve ~ and env vars in path strings."""
    return Path(os.path.expandvars(os.path.expanduser(p)))


def load_config(config_path: Path = None) -> dict:
    """Load unified config file (TOML or JSON)."""
    if config_path is None:
        # Default: look in config/ relative to repo root
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "default.json"
        if not config_path.exists():
            # Fallback: legacy bridge_config.json
            config_path = Path(__file__).resolve().parent.parent.parent / "ai-chain-skill" / "bridge_config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_paths(cfg: dict) -> dict:
    """Resolve all paths from config."""
    data_dir = resolve_path(cfg.get("data_dir", "~/.openclaw/aichain"))
    return {
        "data_dir": data_dir,
        "openclaw_config": resolve_path(cfg.get("openclaw_config", "~/.openclaw/openclaw.json")),
        "state_file": data_dir / "controller_state.json",
        "health_file": data_dir / "health.json",
        "backups_dir": data_dir / "backups",
        "log_file": data_dir / "controller.log",
        "session_dir": data_dir / "sessions",
        "audit_file": data_dir / "audit.jsonl",
        "auth_token_file": data_dir / ".auth_token",
    }


# ─────────────────────────────────────────
# ATOMIC OPS
# ─────────────────────────────────────────

def atomic_write(path: Path, data: dict):
    """Atomic JSON write via tempfile + os.replace."""
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


def safe_read_json(path: Path, default=None):
    """Read JSON with corruption tolerance."""
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default if default is not None else {}


def sha256_of_dict(d: dict) -> str:
    """SHA256 checksum of JSON-serialized dict for integrity checks."""
    raw = json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ─────────────────────────────────────────
# BACKUP
# ─────────────────────────────────────────

def create_backup(config_path: Path, backups_dir: Path, max_backups: int = 3):
    backups_dir.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = backups_dir / f"openclaw.json.bak.{ts}"
    shutil.copy2(config_path, bak)
    backups = sorted(backups_dir.glob("openclaw.json.bak.*"), key=lambda p: p.stat().st_mtime)
    while len(backups) > max_backups:
        backups.pop(0).unlink()
    return bak


def restore_latest_backup(config_path: Path, backups_dir: Path) -> bool:
    backups = sorted(backups_dir.glob("openclaw.json.bak.*"), key=lambda p: p.stat().st_mtime)
    if not backups:
        return False
    latest = backups[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            json.load(f)  # validate
    except (json.JSONDecodeError, IOError):
        return False
    shutil.copy2(latest, config_path)
    return True


# ─────────────────────────────────────────
# PROVIDER RESOLUTION
# ─────────────────────────────────────────

DIRECT_PROVIDERS = {"openai/", "google/", "deepseek/"}


def resolve_model_id(model_id: str) -> str:
    """Convert AIchain model ID to OpenClaw-compatible ID."""
    for prefix in DIRECT_PROVIDERS:
        if model_id.startswith(prefix):
            return model_id
    if not model_id.startswith("openrouter/"):
        return f"openrouter/{model_id}"
    return model_id


# ─────────────────────────────────────────
# STATE MACHINE
# ─────────────────────────────────────────

class Controller:
    """
    Deterministic state machine controller (Brain A).

    Transitions:
        NORMAL     → DEGRADED   (errors detected within window)
        DEGRADED   → ESCALATED  (error count >= threshold)
        DEGRADED   → NORMAL     (errors cleared, window expired)
        ESCALATED  → RECOVERING (success detected)
        ESCALATED  → NORMAL     (TTL expired)
        RECOVERING → NORMAL     (revert complete)

    Circuit breaker:
        CLOSED    → OPEN       (failures hit threshold)
        OPEN      → HALF_OPEN  (cooldown expired)
        HALF_OPEN → CLOSED     (test request succeeds)
        HALF_OPEN → OPEN       (test request fails)
    """

    def __init__(self, cfg: dict, logger: logging.Logger):
        self.cfg = cfg
        self.log = logger
        self.paths = get_paths(cfg)
        self.paths["data_dir"].mkdir(parents=True, exist_ok=True)

        ctrl = cfg.get("controller", {})
        self.error_threshold = ctrl.get("error_threshold", 3)
        self.error_window = ctrl.get("error_window_seconds", 300)
        self.escalation_ttl = ctrl.get("escalation_ttl_minutes", 15)
        self.cooldown = ctrl.get("cooldown_seconds", 30)
        self.poll_interval = ctrl.get("poll_interval_seconds", 3)
        self.max_esc_per_hour = ctrl.get("max_escalations_per_hour", 5)
        self.max_table_age = ctrl.get("routing_table_max_age_hours", 48)
        self.max_backups = cfg.get("max_backups", 3)

        # Runtime state (from file or fresh)
        self._state = self._load_state()

    def _load_state(self) -> dict:
        default = {
            "system": SystemState.NORMAL,
            "circuit": CircuitState.CLOSED,
            "error_timestamps": [],
            "escalation_count_this_hour": 0,
            "escalation_hour": None,
            "escalated_at": None,
            "rescue_model": None,
            "original_primary": None,
            "last_transition": None,
            "last_swap_at": None,
            "godmode": None,
        }
        loaded = safe_read_json(self.paths["state_file"], default)
        # Ensure enum compat
        loaded["system"] = SystemState(loaded.get("system", "NORMAL"))
        loaded["circuit"] = CircuitState(loaded.get("circuit", "CLOSED"))
        return loaded

    def _save_state(self):
        atomic_write(self.paths["state_file"], self._state)

    def _transition(self, new_system: SystemState = None, new_circuit: CircuitState = None, reason: str = ""):
        old_sys = self._state["system"]
        old_cir = self._state["circuit"]
        if new_system:
            self._state["system"] = new_system
        if new_circuit:
            self._state["circuit"] = new_circuit
        self._state["last_transition"] = datetime.now(timezone.utc).isoformat()

        sys_change = f"{old_sys}→{self._state['system']}" if new_system and new_system != old_sys else None
        cir_change = f"{old_cir}→{self._state['circuit']}" if new_circuit and new_circuit != old_cir else None
        changes = [c for c in [sys_change, cir_change] if c]
        if changes:
            self.log.info(f"TRANSITION: {' | '.join(changes)} [{reason}]")
        self._save_state()

    # ── Error tracking ──

    def record_error(self, error_text: str):
        """Record an error event and evaluate thresholds."""
        now = time.time()
        ts_list = self._state["error_timestamps"]
        ts_list.append(now)
        # Prune old entries outside window
        ts_list[:] = [t for t in ts_list if now - t < self.error_window]
        self._save_state()

        self.log.warning(f"Error recorded ({len(ts_list)}/{self.error_threshold}): {error_text[:100]}")

        state = self._state["system"]

        if state == SystemState.NORMAL and len(ts_list) >= 1:
            self._transition(SystemState.DEGRADED, reason=f"error_detected: {error_text[:60]}")

        if state in (SystemState.NORMAL, SystemState.DEGRADED) and len(ts_list) >= self.error_threshold:
            if self._can_escalate():
                return "ESCALATE"
        return None

    def record_success(self):
        """Record a success event."""
        state = self._state["system"]
        self._state["error_timestamps"].clear()

        if state == SystemState.ESCALATED:
            self._transition(SystemState.RECOVERING, reason="success_during_escalation")
            return "REVERT"
        elif state == SystemState.DEGRADED:
            self._transition(SystemState.NORMAL, CircuitState.CLOSED, reason="errors_cleared")
        return None

    # ── Escalation ──

    def _can_escalate(self) -> bool:
        """Check cooldown and rate limits."""
        now = datetime.now(timezone.utc)

        # Cooldown
        last_swap = self._state.get("last_swap_at")
        if last_swap:
            try:
                elapsed = (now - datetime.fromisoformat(last_swap)).total_seconds()
                if elapsed < self.cooldown:
                    self.log.info(f"Cooldown active ({self.cooldown - elapsed:.0f}s remaining)")
                    return False
            except ValueError:
                pass

        # Rate limit
        current_hour = now.strftime("%Y-%m-%d-%H")
        if self._state.get("escalation_hour") != current_hour:
            self._state["escalation_hour"] = current_hour
            self._state["escalation_count_this_hour"] = 0

        if self._state["escalation_count_this_hour"] >= self.max_esc_per_hour:
            self.log.warning(f"Max escalations/hour ({self.max_esc_per_hour}) reached")
            return False

        return True

    def begin_escalation(self, rescue_model: str, original_primary: str, reason: str):
        self._state["rescue_model"] = rescue_model
        self._state["original_primary"] = original_primary
        self._state["escalated_at"] = datetime.now(timezone.utc).isoformat()
        self._state["last_swap_at"] = datetime.now(timezone.utc).isoformat()
        self._state["escalation_count_this_hour"] = self._state.get("escalation_count_this_hour", 0) + 1
        self._state["error_timestamps"].clear()
        self._transition(SystemState.ESCALATED, CircuitState.OPEN, reason=reason)

    def complete_revert(self):
        original = self._state.get("original_primary")
        self._state["rescue_model"] = None
        self._state["original_primary"] = None
        self._state["escalated_at"] = None
        self._state["last_swap_at"] = datetime.now(timezone.utc).isoformat()
        self._transition(SystemState.NORMAL, CircuitState.CLOSED, reason="revert_complete")
        return original

    def check_escalation_ttl(self) -> bool:
        """Returns True if TTL expired and auto-revert needed."""
        if self._state["system"] != SystemState.ESCALATED:
            return False
        escalated_at = self._state.get("escalated_at")
        if not escalated_at:
            return False
        try:
            dt = datetime.fromisoformat(escalated_at)
            age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60
            if age_min > self.escalation_ttl:
                self.log.info(f"Escalation TTL expired ({age_min:.1f}m > {self.escalation_ttl}m)")
                return True
        except ValueError:
            pass
        return False

    # ── God Mode ──

    def set_godmode(self, model: str, original_primary: str):
        self._state["godmode"] = {
            "model": model,
            "original_primary": original_primary,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_state()
        self.log.info(f"GOD MODE: {model}")

    def clear_godmode(self):
        self._state["godmode"] = None
        self._save_state()
        self.log.info("GOD MODE cleared")

    @property
    def is_godmode(self) -> bool:
        return bool(self._state.get("godmode"))

    @property
    def state(self) -> dict:
        return dict(self._state)


# ─────────────────────────────────────────
# CONFIG INJECTION (diff-based)
# ─────────────────────────────────────────

def read_openclaw_config(path: Path) -> dict:
    return safe_read_json(path)


def inject_model(config: dict, primary_id: str, fallback_ids: list[str]) -> dict:
    """Inject model into OpenClaw config. Returns new config."""
    config = copy.deepcopy(config)
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    model_section = defaults.setdefault("model", {})
    whitelist = defaults.setdefault("models", {})

    new_primary = resolve_model_id(primary_id)
    current_primary = model_section.get("primary", "")
    current_fallbacks = model_section.get("fallbacks", [])

    # Build fallback list
    new_fallbacks = []
    seen = {new_primary}
    for fid in fallback_ids:
        oc_id = resolve_model_id(fid)
        if oc_id not in seen:
            new_fallbacks.append(oc_id)
            seen.add(oc_id)
    for fb in current_fallbacks:
        if fb not in seen:
            new_fallbacks.append(fb)
            seen.add(fb)
    if current_primary and current_primary not in seen:
        new_fallbacks.append(current_primary)

    model_section["primary"] = new_primary
    model_section["fallbacks"] = new_fallbacks

    for mid in [new_primary] + new_fallbacks:
        if mid not in whitelist:
            whitelist[mid] = {}

    return config


def config_changed(old: dict, new: dict) -> bool:
    """Check if model config actually changed (diff-based writes)."""
    old_m = old.get("agents", {}).get("defaults", {}).get("model", {})
    new_m = new.get("agents", {}).get("defaults", {}).get("model", {})
    return old_m.get("primary") != new_m.get("primary") or \
           old_m.get("fallbacks") != new_m.get("fallbacks")


def write_config(path: Path, config: dict, backups_dir: Path, max_backups: int = 3, log: logging.Logger = None):
    """Diff-based atomic config write with backup."""
    current = read_openclaw_config(path)
    if not config_changed(current, config):
        if log:
            log.info("Config unchanged — skipping write")
        return False

    create_backup(path, backups_dir, max_backups)
    atomic_write(path, config)
    if log:
        new_primary = config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "?")
        log.info(f"Config written — primary: {new_primary}")
    return True


# ─────────────────────────────────────────
# GATEWAY LOG PATTERNS
# ─────────────────────────────────────────

ERROR_PATTERNS = re.compile(
    r"(429|rate.?limit|503|overloaded|502|bad.?gateway|"
    r"401|unauthorized|auth.?error|ECONNREFUSED|ETIMEDOUT|"
    r"timeout|loop.?detect|retry.?exhaust|"
    r"reasoning.?loop|provider.?error|model.?unavailable|"
    r"Config invalid|Unrecognized key|fetch.?failed)", re.IGNORECASE
)

SUCCESS_PATTERNS = re.compile(
    r"(200\s+OK|response.?received|completion.?success|"
    r"stream.?complete|tokens.?generated|finish_reason|"
    r"\"done\":\s*true|\"status\":\s*\"ok\")", re.IGNORECASE
)
