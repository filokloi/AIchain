#!/usr/bin/env python3
"""Tests for operational verification helpers."""

from tools.verify_live_dashboard import classify_live_dashboard_status
from tools.verify_local_execution import (
    CapacityEstimate,
    classify_local_execution_status,
    parse_lmstudio_estimate_output,
    refine_local_execution_readiness,
)


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



def test_live_dashboard_classification_detects_deploy_confirmed_with_rollback():
    result = classify_live_dashboard_status(
        index_html="<script>fetch('catalog_manifest.json')</script><script>fetch('ai_routing_table.json')</script>",
        manifest_text='{"manifest_type":"aichain.catalog","public_artifact_readiness":{"dashboard_switch_ready":true},"canonical_public_artifact":{"migration_state":"safe_to_switch_dashboard_to_canonical_artifact"}}',
        site_http_ok=True,
        manifest_http_ok=True,
    )
    assert result.status == "deploy_confirmed_with_rollback"
    assert result.site_uses_legacy is True
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



def test_parse_lmstudio_estimate_output_detects_capacity_block():
    estimate = parse_lmstudio_estimate_output(
        "Estimated Total Memory: 6.38 GiB\nEstimate: This model will fail to load based on your resource guardrails settings."
    )

    assert estimate.status == "machine_capacity_blocked"
    assert estimate.estimated_total_memory_gib == 6.38



def test_refine_local_execution_readiness_marks_runtime_not_reached_when_completion_probe_fails():
    status = classify_local_execution_status(True, True, True, "lmstudio/qwen/qwen3.5-9b")

    readiness = refine_local_execution_readiness(
        status,
        completion_probe_ok=False,
        completion_probe_detail="timeout",
        completion_probe_provider="lmstudio",
        completion_probe_model="lmstudio/qwen/qwen3.5-9b",
    )

    assert readiness.effective_status == "target_form_not_reached"
    assert "local completion probe failed" in readiness.reasons



def test_refine_local_execution_readiness_marks_capacity_block():
    status = classify_local_execution_status(True, True, True, "lmstudio/qwen/qwen3.5-9b")

    readiness = refine_local_execution_readiness(
        status,
        completion_probe_ok=False,
        completion_probe_detail="timeout",
        completion_probe_provider="lmstudio",
        completion_probe_model="lmstudio/qwen/qwen3.5-9b",
        capacity_estimate=CapacityEstimate(
            status="machine_capacity_blocked",
            detail="Estimated Total Memory: 6.38 GiB",
            estimated_total_memory_gib=6.38,
        ),
    )

    assert readiness.effective_status == "target_form_not_reached"
    assert readiness.capacity_status == "machine_capacity_blocked"
    assert "machine_capacity_blocked" in readiness.reasons



def test_refine_local_execution_readiness_marks_activation_candidate_when_disabled_but_probe_succeeds():
    status = classify_local_execution_status(False, False, False, "")

    readiness = refine_local_execution_readiness(
        status,
        completion_probe_ok=True,
        completion_probe_detail="OK",
        completion_probe_provider="lmstudio",
        completion_probe_model="lmstudio/qwen/qwen3.5-9b",
    )

    assert readiness.effective_status == "disabled"
    assert readiness.activation_ready is True
    assert "local runtime activation candidate ready" in readiness.reasons


def test_refine_local_execution_readiness_keeps_runtime_confirmed_when_completion_succeeds_despite_capacity_warning():
    status = classify_local_execution_status(True, True, True, "lmstudio/qwen/qwen3-4b-thinking-2507")

    readiness = refine_local_execution_readiness(
        status,
        completion_probe_ok=True,
        completion_probe_detail="OK",
        completion_probe_provider="lmstudio",
        completion_probe_model="lmstudio/qwen/qwen3-4b-thinking-2507",
        capacity_estimate=CapacityEstimate(
            status="machine_capacity_blocked",
            detail="Estimated Total Memory: 2.50 GiB",
            estimated_total_memory_gib=2.5,
        ),
    )

    assert readiness.effective_status == "runtime_confirmed"
    assert "machine_capacity_blocked" in readiness.reasons
