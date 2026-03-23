#!/usr/bin/env python3
"""
Tests for aichaind.core.session — CanonicalSession, SessionStore

Covers:
- Session creation with defaults
- Turn advancement
- Provider run recording
- Budget tracking
- Privacy context
- File-backed persistence
- Serialization round-trip
"""

import pytest
import tempfile
import shutil
import threading
from pathlib import Path

from aichaind.core.session import (
    CanonicalSession, SessionStore, ProviderRun,
    PrivacyContext, BudgetState, SummaryState,
)


@pytest.fixture
def tmp_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def store(tmp_dir):
    return SessionStore(tmp_dir)


class TestCanonicalSession:
    def test_default_session_creation(self):
        s = CanonicalSession(session_id="test-123", created_at="2026-01-01T00:00:00Z")
        assert s.session_id == "test-123"
        assert s.turn_index == 0
        assert s.routing_mode == "auto"
        assert s.routing_preference == "balanced"
        assert s.locked_model == ""
        assert s.locked_provider == ""
        assert s.system_state == "NORMAL"
        assert s.circuit_state == "CLOSED"

    def test_advance_turn(self):
        s = CanonicalSession(session_id="t1")
        assert s.turn_index == 0
        s.advance_turn()
        assert s.turn_index == 1
        s.advance_turn()
        assert s.turn_index == 2
        assert s.updated_at != ""

    def test_record_run_tracks_cost(self):
        s = CanonicalSession(session_id="t2")
        run = ProviderRun(model="test/model", cost_usd=0.05, status="success")
        s.record_run(run)
        assert len(s.provider_runs) == 1
        assert s.budget_state.total_spent_usd == 0.05

    def test_record_multiple_runs_accumulate_cost(self):
        s = CanonicalSession(session_id="t3")
        s.record_run(ProviderRun(model="a", cost_usd=0.10))
        s.record_run(ProviderRun(model="b", cost_usd=0.20))
        assert abs(s.budget_state.total_spent_usd - 0.30) < 1e-9

    def test_serialization_roundtrip(self):
        s = CanonicalSession(session_id="rt1")
        s.advance_turn()
        s.record_run(ProviderRun(model="test/m", cost_usd=0.01, status="success"))
        s.privacy_context.contains_pii = True
        s.privacy_context.pii_categories = ["email"]
        s.summary_state.pinned_facts = ["fact_one"]
        s.routing_mode = "manual"
        s.routing_preference = "min_cost"
        s.locked_model = "openai-codex/gpt-5.4"
        s.locked_provider = "openai-codex"

        d = s.to_dict()
        assert d["session_id"] == "rt1"
        assert d["turn_index"] == 1
        assert d["routing_mode"] == "manual"
        assert d["routing_preference"] == "min_cost"
        assert d["locked_model"] == "openai-codex/gpt-5.4"
        assert d["locked_provider"] == "openai-codex"
        assert d["privacy_context"]["contains_pii"] is True
        assert d["summary_state"]["pinned_facts"] == ["fact_one"]


class TestBudgetState:
    def test_over_budget(self):
        b = BudgetState(total_spent_usd=10.0, session_limit_usd=10.0)
        assert b.over_budget is True
        assert b.remaining_usd == 0.0

    def test_under_budget(self):
        b = BudgetState(total_spent_usd=3.0, session_limit_usd=10.0)
        assert b.over_budget is False
        assert b.remaining_usd == 7.0

    def test_near_limit(self):
        b = BudgetState(total_spent_usd=8.5, session_limit_usd=10.0, warn_threshold_pct=0.8)
        assert b.near_limit is True

    def test_not_near_limit(self):
        b = BudgetState(total_spent_usd=2.0, session_limit_usd=10.0)
        assert b.near_limit is False


class TestSessionStore:
    def test_create_session(self, store):
        s = store.create()
        assert s.session_id != ""
        assert s.created_at != ""

    def test_create_session_with_explicit_id(self, store):
        s = store.create(session_id="manual-lock-demo")
        assert s.session_id == "manual-lock-demo"
        loaded = store.load("manual-lock-demo")
        assert loaded is not None
        assert loaded.session_id == "manual-lock-demo"

    def test_save_and_load(self, store):
        s = store.create()
        s.advance_turn()
        s.advance_turn()
        s.summary_state.pinned_facts = ["important_fact"]
        store.save(s)

        loaded = store.load(s.session_id)
        assert loaded is not None
        assert loaded.turn_index == 2
        assert loaded.summary_state.pinned_facts == ["important_fact"]

    def test_persist_manual_routing_state(self, store):
        s = store.create()
        s.routing_mode = "manual"
        s.routing_preference = "max_intelligence"
        s.locked_model = "openai-codex/gpt-5.4"
        s.locked_provider = "openai-codex"
        store.save(s)

        loaded = store.load(s.session_id)
        assert loaded is not None
        assert loaded.routing_mode == "manual"
        assert loaded.routing_preference == "max_intelligence"
        assert loaded.locked_model == "openai-codex/gpt-5.4"
        assert loaded.locked_provider == "openai-codex"

    def test_load_nonexistent_returns_none(self, store):
        assert store.load("nonexistent-id") is None

    def test_delete_session(self, store):
        s = store.create()
        sid = s.session_id
        assert store.delete(sid) is True
        assert store.load(sid) is None

    def test_delete_nonexistent(self, store):
        assert store.delete("nonexistent") is False

    def test_persist_provider_runs(self, store):
        s = store.create()
        s.record_run(ProviderRun(
            run_id="r1", model="openai/gpt-4o",
            provider="openai", latency_ms=450.0,
            input_tokens=100, output_tokens=200,
            cost_usd=0.003, status="success"
        ))
        store.save(s)

        loaded = store.load(s.session_id)
        assert len(loaded.provider_runs) == 1
        assert loaded.provider_runs[0].model == "openai/gpt-4o"
        assert loaded.provider_runs[0].latency_ms == 450.0
        assert loaded.budget_state.total_spent_usd == 0.003

    def test_concurrent_load_save_does_not_raise(self, store):
        session = store.create(session_id="race-proof")
        errors = []

        def saver():
            try:
                for idx in range(30):
                    session.request_status = "running" if idx % 2 == 0 else "idle"
                    session.request_label = "Thinking…" if idx % 2 == 0 else ""
                    store.save(session)
            except Exception as exc:
                errors.append(exc)

        def loader():
            try:
                for _ in range(30):
                    loaded = store.load("race-proof")
                    assert loaded is not None
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=saver),
            threading.Thread(target=loader),
            threading.Thread(target=saver),
            threading.Thread(target=loader),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []

