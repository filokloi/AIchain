#!/usr/bin/env python3
"""Tests for operational verification helpers."""

from tools.verify_live_dashboard import classify_live_dashboard_status
from tools.verify_local_execution import classify_local_execution_status


def test_live_dashboard_classification_detects_not_switched_state():
    result = classify_live_dashboard_status(
        index_html="<script>fetch('ai_routing_table.json')</script>",
        manifest_text="",
        site_http_ok=True,
        manifest_http_ok=False,
    )
    assert result.status == "deploy_not_switched"
    assert result.site_uses_canonical is False
    assert result.manifest_http_ok is False


def test_live_dashboard_classification_detects_canonical_candidate():
    result = classify_live_dashboard_status(
        index_html="<script>fetch('catalog_manifest.json')</script>",
        manifest_text='{"manifest_type":"aichain.catalog","public_artifact_readiness":{"dashboard_switch_ready":true},"canonical_public_artifact":{"migration_state":"safe_to_switch_dashboard_to_canonical_artifact"}}',
        site_http_ok=True,
        manifest_http_ok=True,
    )
    assert result.status == "deploy_confirmed_candidate"
    assert result.site_uses_canonical is True
    assert result.manifest_valid is True


def test_local_execution_classification_disabled():
    result = classify_local_execution_status(False, False, False, "")
    assert result.status == "disabled"


def test_local_execution_classification_unreachable():
    result = classify_local_execution_status(True, True, False, "local/qwen2.5-coder")
    assert result.status == "configured_but_unreachable"


def test_local_execution_classification_runtime_confirmed():
    result = classify_local_execution_status(True, True, True, "local/qwen2.5-coder")
    assert result.status == "runtime_confirmed"
