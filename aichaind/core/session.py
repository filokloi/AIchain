#!/usr/bin/env python3
"""
aichaind.core.session — Canonical Local Session State

The single source of truth for all session data.
Provider-side state is never the sole authority.
"""

import uuid
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aichaind.core.state_machine import atomic_write, safe_read_json, get_paths


# ─────────────────────────────────────────
# SUB-MODELS
# ─────────────────────────────────────────

@dataclass
class ProviderRun:
    """Record of a single provider execution."""
    run_id: str = ""
    model: str = ""
    provider: str = ""
    timestamp: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    status: str = "pending"  # pending, success, error, timeout
    error_text: str = ""


@dataclass
class PrivacyContext:
    """Privacy state for the session."""
    contains_pii: bool = False
    pii_categories: list[str] = field(default_factory=list)
    cloud_routing_allowed: bool = True


@dataclass
class BudgetState:
    """Budget tracking for the session."""
    total_spent_usd: float = 0.0
    session_limit_usd: float = 10.0   # default $10 per session
    per_turn_limit_usd: float = 1.0   # default $1 per turn
    warn_threshold_pct: float = 0.8   # warn at 80% of limit

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.session_limit_usd - self.total_spent_usd)

    @property
    def over_budget(self) -> bool:
        return self.total_spent_usd >= self.session_limit_usd

    @property
    def near_limit(self) -> bool:
        return self.total_spent_usd >= (self.session_limit_usd * self.warn_threshold_pct)


@dataclass
class SummaryState:
    """Context compression state."""
    rolling_summary: str = ""
    pinned_facts: list[str] = field(default_factory=list)
    active_plan: str = ""
    open_loops: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)


# ─────────────────────────────────────────
# CANONICAL SESSION
# ─────────────────────────────────────────

@dataclass
class CanonicalSession:
    """
    The canonical local session state — single source of truth.
    Provider-side state is never relied upon as the sole authority.
    """
    session_id: str = ""
    turn_index: int = 0
    created_at: str = ""
    updated_at: str = ""
    routing_mode: str = "auto"
    routing_preference: str = "balanced"
    locked_model: str = ""
    locked_provider: str = ""
    provider_runs: list[ProviderRun] = field(default_factory=list)
    privacy_context: PrivacyContext = field(default_factory=PrivacyContext)
    budget_state: BudgetState = field(default_factory=BudgetState)
    summary_state: SummaryState = field(default_factory=SummaryState)
    redaction_map: dict = field(default_factory=dict)
    telemetry_refs: list[str] = field(default_factory=list)
    cache_refs: list[str] = field(default_factory=list)
    system_state: str = "NORMAL"
    circuit_state: str = "CLOSED"

    def advance_turn(self):
        """Increment turn counter and update timestamp."""
        self.turn_index += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def record_run(self, run: ProviderRun):
        """Record a provider execution."""
        self.provider_runs.append(run)
        if run.cost_usd > 0:
            self.budget_state.total_spent_usd += run.cost_usd
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return asdict(self)


# ─────────────────────────────────────────
# SESSION STORE
# ─────────────────────────────────────────

class SessionStore:
    """File-backed session store using atomic writes."""

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def create(self, session_id: str = "") -> CanonicalSession:
        """Create a new session."""
        now = datetime.now(timezone.utc).isoformat()
        session = CanonicalSession(
            session_id=str(session_id or uuid.uuid4()),
            created_at=now,
            updated_at=now,
        )
        self.save(session)
        return session

    def load(self, session_id: str) -> Optional[CanonicalSession]:
        """Load a session by ID."""
        path = self.session_dir / f"{session_id}.json"
        data = safe_read_json(path)
        if not data:
            return None
        try:
            # Reconstruct nested dataclasses
            session = CanonicalSession(
                session_id=data.get("session_id", ""),
                turn_index=data.get("turn_index", 0),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                routing_mode=str(data.get("routing_mode", "auto") or "auto"),
                routing_preference=str(data.get("routing_preference", "balanced") or "balanced"),
                locked_model=str(data.get("locked_model", "") or ""),
                locked_provider=str(data.get("locked_provider", "") or ""),
                privacy_context=PrivacyContext(**data.get("privacy_context", {})),
                budget_state=BudgetState(**{k: v for k, v in data.get("budget_state", {}).items()
                                           if k in BudgetState.__dataclass_fields__}),
                summary_state=SummaryState(**data.get("summary_state", {})),
                redaction_map=data.get("redaction_map", {}),
                telemetry_refs=data.get("telemetry_refs", []),
                cache_refs=data.get("cache_refs", []),
                system_state=data.get("system_state", "NORMAL"),
                circuit_state=data.get("circuit_state", "CLOSED"),
            )
            # Reconstruct provider runs
            for run_data in data.get("provider_runs", []):
                session.provider_runs.append(ProviderRun(**run_data))
            return session
        except Exception:
            return None

    def save(self, session: CanonicalSession):
        """Persist session with atomic write."""
        path = self.session_dir / f"{session.session_id}.json"
        atomic_write(path, session.to_dict())

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        path = self.session_dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
