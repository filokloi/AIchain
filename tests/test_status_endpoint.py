#!/usr/bin/env python3
"""Regression tests for the /status endpoint.

/status previously raised UnboundLocalError when the discovery report had no
`timestamp` attribute (the normal case right after boot), returning HTTP 500.
"""

from types import SimpleNamespace

import aichaind.transport.http_server as http_server


class _DummyController:
    state = {"system": "NORMAL", "circuit": "CLOSED"}


def _make_handler(captured):
    handler = object.__new__(http_server.AichainDHandler)

    def _capture_send_json(status_code, data, extra_headers=None):
        captured["status_code"] = status_code
        captured["data"] = data

    handler._send_json = _capture_send_json
    return handler


def _call_status(monkeypatch, discovery_report):
    captured = {}
    monkeypatch.setattr(http_server, "_controller", _DummyController())
    monkeypatch.setattr(http_server, "_discovery_report", discovery_report)
    monkeypatch.setattr(http_server, "_roles", {"fast_brain": "a/b", "heavy_brain": "c/d"})
    monkeypatch.setattr(http_server, "_auth_manager", None)
    monkeypatch.setattr(http_server, "_operator_metrics", None)
    monkeypatch.setattr(http_server, "_provider_access_layer", None)
    monkeypatch.setattr(http_server, "_SERVER_START_TIME", 0.0)

    handler = _make_handler(captured)
    handler._handle_status()
    return captured


def test_status_succeeds_without_discovery_timestamp(monkeypatch):
    """No discovery report at all -> 200 with catalog_age_seconds = None."""
    captured = _call_status(monkeypatch, discovery_report=None)
    assert captured["status_code"] == 200
    assert captured["data"]["status"] == "ok"
    assert captured["data"]["catalog_age_seconds"] is None


def test_status_succeeds_when_report_lacks_timestamp(monkeypatch):
    """Discovery report without a timestamp attribute -> 200, age None."""
    report = SimpleNamespace(credentials=[], direct_providers=[])
    captured = _call_status(monkeypatch, discovery_report=report)
    assert captured["status_code"] == 200
    assert captured["data"]["catalog_age_seconds"] is None


def test_status_reports_catalog_age_when_timestamp_present(monkeypatch):
    """Discovery report with a timestamp -> age is a non-negative number."""
    import time
    report = SimpleNamespace(timestamp=time.time() - 30)
    captured = _call_status(monkeypatch, discovery_report=report)
    assert captured["status_code"] == 200
    age = captured["data"]["catalog_age_seconds"]
    assert age is not None and 25 <= age <= 60


def test_status_includes_expected_fields(monkeypatch):
    captured = _call_status(monkeypatch, discovery_report=None)
    data = captured["data"]
    for field in ("status", "version", "uptime_seconds", "system_state",
                  "routing_mode", "provider_health", "roles", "auth_active"):
        assert field in data, f"missing field: {field}"
