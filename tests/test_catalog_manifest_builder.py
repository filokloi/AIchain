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
        "total_models_analyzed": 4,
        "tier_breakdown": {"OAUTH_BRIDGE": 1, "FREE_FRONTIER": 2, "HEAVY_HITTER": 1},
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
            {
                "model": "qwen/qwen2.5-7b-instruct",
                "provider": "Qwen",
                "tier": "FREE_FRONTIER",
                "metrics": {
                    "intelligence": 74,
                    "speed": 74,
                    "stability": 81,
                    "cost": 0.0,
                },
                "raw_metrics": {
                    "context_length": 131072,
                    "aa_quality": 71.0,
                    "lmsys_elo": 1180.0,
                },
                "source_attribution": {
                    "catalog": ["openrouter"],
                    "intelligence": "benchmark",
                },
                "self_hosting": {
                    "self_hostable": True,
                    "open_weight": True,
                    "family": "Qwen",
                    "hosting_modes": ["cloud_api", "self_hosted"],
                    "download_sources": ["huggingface", "ollama", "lmstudio"],
                    "preferred_runtimes": ["ollama", "lmstudio", "vllm"],
                    "quantizations_known": ["BF16", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"],
                    "parameter_scale_billions": 7.0,
                    "hardware_profile_hint": "16gb_memory_class_recommended",
                    "self_hosting_notes": "Open-weight Qwen family. Practical viability depends on quantization choice and local memory budget.",
                    "benchmark_evidence_sources": ["openrouter_catalog", "curated_benchmark_map", "artificial_analysis", "lmsys_arena"],
                },
                "rank": 4,
            },
        ],
    }


def test_derive_roles_from_legacy_table():
    roles = derive_roles(_table())
    assert roles["fast"] == "google/gemini-2.5-flash"
    assert roles["heavy"] == "openai/o3-pro"
    assert roles["visual"] == "openai/gpt-4o"


def test_build_manifest_outputs_native_v5_contract():
    manifest = build_manifest(_table(), base_url="https://example.test/AIchain", provider_access_runtime={})
    assert manifest["schema_version"] == "5.0.0"
    assert manifest["manifest_type"] == "aichain.catalog"
    assert manifest["planes"]["global"]["kind"] == "catalog"
    assert manifest["planes"]["local"]["kind"] == "execution"
    assert manifest["planes"]["global"]["manifest_url"] == "https://example.test/AIchain/catalog_manifest.json"
    assert manifest["roles"]["heavy"]["model"] == "openai/o3-pro"
    assert manifest["catalog"]["legacy_feed_version"] == "4.0-sovereign"
    assert manifest["catalog"]["self_hostable_models"] == 1
    assert manifest["operational_status"]["sources"]["openrouter"]["runtime_confirmed"] is True
    assert manifest["public_artifact_readiness"]["recommended_state"] == "hold_legacy_dashboard_view"
    assert manifest["live_promos"] == ["Gemini Flash promo"]
    assert manifest["provider_access_matrix"]["openai-codex"]["overall_mode"] == "target_form_not_reached"
    assert manifest["provider_access_matrix"]["openai-codex"]["methods"]["oauth"]["mode"] == "target_form_not_reached"
    assert manifest["provider_access_matrix"]["openai-codex"]["methods"]["oauth"]["target_model"] == "openai-codex/gpt-5.4"
    assert manifest["self_hosted_model_index"]["total_models"] == 1
    assert manifest["self_hosted_model_index"]["entries"][0]["model"] == "qwen/qwen2.5-7b-instruct"
    assert len(manifest["routing_hierarchy"]) == 4


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


def test_build_manifest_merges_provider_access_runtime_snapshot():
    runtime_snapshot = {
        "providers": {
            "openai-codex": {
                "overall_mode": "runtime_confirmed",
                "factual_state": "runtime_confirmed",
                "last_verified_at": "2026-03-10T16:24:00Z",
                "project_verification": "Runtime probe confirmed openai-codex/gpt-5.4 on the maintainer machine.",
                "methods": {
                    "oauth": {
                        "mode": "runtime_confirmed",
                        "runtime_confirmed": True,
                        "last_verified_at": "2026-03-10T16:24:00Z",
                        "verified_models": ["openai-codex/gpt-5.4"],
                        "target_form_reached": True,
                    }
                },
            }
        }
    }

    manifest = build_manifest(_table(), base_url="https://example.test/AIchain", provider_access_runtime=runtime_snapshot)
    codex = manifest["provider_access_matrix"]["openai-codex"]
    assert codex["overall_mode"] == "runtime_confirmed"
    assert codex["factual_state"] == "runtime_confirmed"
    assert codex["last_verified_at"] == "2026-03-10T16:24:00Z"
    assert codex["methods"]["oauth"]["mode"] == "runtime_confirmed"
    assert codex["methods"]["oauth"]["runtime_confirmed"] is True
    assert codex["methods"]["oauth"]["verified_models"] == ["openai-codex/gpt-5.4"]
    assert codex["methods"]["oauth"]["official_support"] is True
