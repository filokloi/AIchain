#!/usr/bin/env python3
"""
Tests for aichaind.telemetry.audit — AuditLogger

Covers:
- Writing audit entries
- Typed record methods
- Tail reading
- File creation
- Append-only behavior
"""

import pytest
import tempfile
import shutil
import json
from pathlib import Path

from aichaind.telemetry.audit import AuditLogger, AuditEntry


@pytest.fixture
def audit_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def logger(audit_dir):
    return AuditLogger(audit_dir / "audit.jsonl")


class TestAuditLogger:
    def test_record_creates_file(self, logger):
        logger.record("test_action")
        assert logger.audit_path.exists()

    def test_record_returns_trace_id(self, logger):
        tid = logger.record("test")
        assert tid != ""
        assert len(tid) == 8

    def test_record_writes_json_line(self, logger):
        logger.record("test_action", {"key": "value"})
        content = logger.audit_path.read_text(encoding="utf-8")
        entry = json.loads(content.strip())
        assert entry["action"] == "test_action"
        assert entry["details"]["key"] == "value"
        assert entry["timestamp"] != ""

    def test_append_only(self, logger):
        logger.record("action1")
        logger.record("action2")
        logger.record("action3")
        lines = logger.audit_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_record_route(self, logger):
        tid = logger.record_route("openai/gpt-4o", 0.95, ["L1:godmode"], latency_ms=1.5)
        entries = logger.tail(1)
        assert entries[0]["action"] == "route"
        assert entries[0]["details"]["model"] == "openai/gpt-4o"
        assert entries[0]["details"]["confidence"] == 0.95

    def test_record_escalation(self, logger):
        logger.record_escalation("rescue/m", "primary/m", "error_threshold")
        entries = logger.tail(1)
        assert entries[0]["action"] == "escalate"
        assert entries[0]["details"]["rescue_model"] == "rescue/m"

    def test_record_godmode(self, logger):
        logger.record_godmode("openai/o3-pro", action="activate")
        entries = logger.tail(1)
        assert entries[0]["action"] == "godmode"
        assert entries[0]["actor"] == "user"

    def test_record_auth_failure(self, logger):
        logger.record_auth_failure("invalid_token")
        entries = logger.tail(1)
        assert entries[0]["action"] == "auth_fail"

    def test_tail_respects_count(self, logger):
        for i in range(10):
            logger.record(f"action_{i}")
        entries = logger.tail(3)
        assert len(entries) == 3
        assert entries[0]["action"] == "action_7"
        assert entries[2]["action"] == "action_9"

    def test_tail_empty_file(self, logger):
        entries = logger.tail(5)
        assert entries == []


class TestAuditEntry:
    def test_auto_timestamp(self):
        e = AuditEntry(action="test")
        assert e.timestamp != ""

    def test_auto_trace_id(self):
        e = AuditEntry(action="test")
        assert e.trace_id != ""
        assert len(e.trace_id) == 8

    def test_explicit_values(self):
        e = AuditEntry(timestamp="2026-01-01", trace_id="abc12345", action="x")
        assert e.timestamp == "2026-01-01"
        assert e.trace_id == "abc12345"
