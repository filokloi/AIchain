#!/usr/bin/env python3
"""Tests for building the native v5 public catalog manifest."""

from tools.build_catalog_manifest import build_manifest, derive_roles


def _table() -> dict:
    return {
        "system_status": "OPERATIONAL",
        "scope": "GLOBAL_NON_DISCRIMINATORY",
        "version": "4.0-sovereign",
        "last_synopsis": "2026-03-08T10:00:00+00:00",
        "data_sources": {"openrouter": 100},
        "total_models_analyzed": 3,
        "tier_breakdown": {"OAUTH_BRIDGE": 1, "FREE_FRONTIER": 1, "HEAVY_HITTER": 1},
        "heavy_hitter": {
            "model": "openai/o3-pro",
            "intelligence": 99,
            "note": "Global rescue model",
        },
        "operational_status": {
            "sources": {
                "openrouter": {
                    "implemented_in_code": True,
                    "runtime_confirmed": True,
                    "mode": "runtime_confirmed",
                },
                "lmsys": {
                    "implemented_in_code": True,
                    "runtime_confirmed": False,
                    "mode": "degraded_fallback",
                },
            },
            "helper_ai": {
                "gemini": {
                    "implemented_in_code": True,
                    "runtime_confirmed": True,
                    "mode": "runtime_confirmed",
                    "role": "primary",
                },
                "groq": {
                    "implemented_in_code": True,
                    "runtime_confirmed": False,
                    "mode": "blocked_missing_credentials",
                    "role": "fallback",
                },
            },
        },
        "live_promos": ["Gemini Flash promo"],
        "public_artifact_readiness": {
            "dashboard_switch_ready": False,
            "recommended_state": "hold_legacy_dashboard_view",
            "blockers": ["global control plane is still operating in degraded mode"],
        },
        "routing_hierarchy": [
            {
                "model": "google/gemini-2.5-flash",
                "provider": "Google",
                "tier": "FREE_FRONTIER",
                "metrics": {
                    "intelligence": 85,
                    "speed": 95,
                    "stability": 91,
                    "cost": 0.0,
                },
                "rank": 1,
            },
            {
                "model": "openai/o3-pro",
                "provider": "OpenAI",
                "tier": "HEAVY_HITTER",
                "metrics": {
                    "intelligence": 99,
                    "speed": 84,
                    "stability": 96,
                    "cost": 0.01,
                },
                "rank": 2,
            },
            {
                "model": "openai/gpt-4o",
                "provider": "OpenAI",
                "tier": "OAUTH_BRIDGE",
                "metrics": {
                    "intelligence": 92,
                    "speed": 89,
                    "stability": 95,
                    "cost": 0.0,
                },
                "rank": 3,
            },
        ],
    }


def test_derive_roles_from_legacy_table():
    roles = derive_roles(_table())
    assert roles["fast"] == "google/gemini-2.5-flash"
    assert roles["heavy"] == "openai/o3-pro"
    assert roles["visual"] == "openai/gpt-4o"


def test_build_manifest_outputs_native_v5_contract():
    manifest = build_manifest(_table(), base_url="https://example.test/AIchain")
    assert manifest["schema_version"] == "5.0.0"
    assert manifest["manifest_type"] == "aichain.catalog"
    assert manifest["planes"]["global"]["kind"] == "catalog"
    assert manifest["planes"]["local"]["kind"] == "execution"
    assert manifest["planes"]["global"]["manifest_url"] == "https://example.test/AIchain/catalog_manifest.json"
    assert manifest["roles"]["heavy"]["model"] == "openai/o3-pro"
    assert manifest["catalog"]["legacy_feed_version"] == "4.0-sovereign"
    assert manifest["operational_status"]["sources"]["openrouter"]["runtime_confirmed"] is True
    assert manifest["public_artifact_readiness"]["recommended_state"] == "hold_legacy_dashboard_view"
    assert manifest["live_promos"] == ["Gemini Flash promo"]
    assert len(manifest["routing_hierarchy"]) == 3


def test_fast_role_does_not_default_to_heavy_reasoning_model():
    table = _table()
    table["routing_hierarchy"].insert(0, {
        "model": "openai/o3-pro",
        "provider": "OpenAI",
        "tier": "OAUTH_BRIDGE",
        "metrics": {
            "intelligence": 99,
            "speed": 99,
            "stability": 98,
            "cost": 0.00005,
        },
        "rank": 0,
    })
    roles = derive_roles(table)
    assert roles["fast"] != "openai/o3-pro"
    assert roles["heavy"] == "openai/o3-pro"
