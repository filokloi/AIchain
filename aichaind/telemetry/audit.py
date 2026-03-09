#!/usr/bin/env python3
"""
aichaind.telemetry.audit — Immutable Audit Trail

Append-only audit log for all routing decisions, escalations,
godmode activations, and config changes.
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict

log = logging.getLogger("aichaind.telemetry.audit")


@dataclass
class AuditEntry:
    """A single audit record."""
    timestamp: str = ""
    trace_id: str = ""
    action: str = ""          # route, escalate, godmode, config_change, auth_fail
    actor: str = "system"     # system, user, policy
    details: dict = None
    session_id: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.trace_id:
            self.trace_id = str(uuid.uuid4())[:8]
        if self.details is None:
            self.details = {}


class AuditLogger:
    """Write-only audit trail. Append-only, never modified or truncated."""

    def __init__(self, audit_path: Path):
        self.audit_path = audit_path
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, action: str, details: dict = None, actor: str = "system",
               session_id: str = "", trace_id: str = "") -> str:
        """Record an audit entry. Returns the trace_id."""
        entry = AuditEntry(
            action=action,
            actor=actor,
            details=details or {},
            session_id=session_id,
            trace_id=trace_id or str(uuid.uuid4())[:8],
        )
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception as e:
            log.error(f"Audit write failed: {e}")
        return entry.trace_id

    def record_route(self, model: str, confidence: float, layers: list,
                     latency_ms: float = 0, session_id: str = "") -> str:
        return self.record("route", {
            "model": model, "confidence": confidence,
            "layers": layers, "latency_ms": latency_ms,
        }, session_id=session_id)

    def record_escalation(self, rescue_model: str, original_model: str,
                          reason: str, session_id: str = "") -> str:
        return self.record("escalate", {
            "rescue_model": rescue_model,
            "original_model": original_model,
            "reason": reason,
        }, session_id=session_id)

    def record_godmode(self, model: str, action: str = "activate",
                       session_id: str = "") -> str:
        return self.record("godmode", {
            "model": model, "action": action,
        }, actor="user", session_id=session_id)

    def record_auth_failure(self, reason: str = "") -> str:
        return self.record("auth_fail", {"reason": reason})

    def tail(self, n: int = 20) -> list[dict]:
        """Read last N entries from audit log."""
        if not self.audit_path.exists():
            return []
        try:
            with open(self.audit_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            entries = []
            for line in lines[-n:]:
                try:
                    entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
            return entries
        except Exception:
            return []
