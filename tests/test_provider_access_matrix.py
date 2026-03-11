#!/usr/bin/env python3
"""Tests for provider access matrix runtime merging and release verification."""

import pytest

from tools.catalog_pipeline.provider_access import build_provider_access_matrix
from tools.verify_dashboard_release import VerificationError, verify_manifest


def test_build_provider_access_matrix_uses_runtime_snapshot_for_codex_target_form():
    runtime_snapshot = {
        "providers": {
            "openai-codex": {
                "overall_mode": "runtime_confirmed",
                "factual_state": "runtime_confirmed",
                "last_verified_at": "2026-03-10T18:00:00Z",
                "methods": {
                    "oauth": {
                        "mode": "runtime_confirmed",
                        "runtime_confirmed": True,
                        "verified_models": ["openai-codex/gpt-5.4"],
                        "target_model": "openai-codex/gpt-5.4",
                        "last_verified_at": "2026-03-10T18:00:00Z",
                    }
                },
            }
        }
    }

    matrix = build_provider_access_matrix(runtime_snapshot)
    codex = matrix["openai-codex"]

    assert codex["overall_mode"] == "runtime_confirmed"
    assert codex["factual_state"] == "runtime_confirmed"
    assert codex["runtime_confirmed_methods"] == ["oauth"]
    assert codex["methods"]["oauth"]["mode"] == "runtime_confirmed"
    assert codex["methods"]["oauth"]["official_support"] is True
    assert codex["methods"]["oauth"]["target_model"] == "openai-codex/gpt-5.4"


def test_verify_dashboard_release_requires_provider_access_matrix():
    manifest = {
        "manifest_type": "aichain.catalog",
        "public_artifact_readiness": {"dashboard_switch_ready": True},
        "canonical_public_artifact": {"migration_state": "safe_to_switch_dashboard_to_canonical_artifact"},
    }

    with pytest.raises(VerificationError, match="provider_access_matrix"):
        verify_manifest(manifest)


def test_verify_dashboard_release_accepts_valid_provider_access_matrix():
    manifest = {
        "manifest_type": "aichain.catalog",
        "public_artifact_readiness": {"dashboard_switch_ready": True},
        "canonical_public_artifact": {"migration_state": "safe_to_switch_dashboard_to_canonical_artifact"},
        "provider_access_matrix": {
            "openai": {"methods": {"api_key": {"mode": "runtime_confirmed"}}},
            "openrouter": {"methods": {"api_key": {"mode": "runtime_confirmed"}}},
            "deepseek": {"methods": {"api_key": {"mode": "runtime_confirmed"}}},
            "openai-codex": {
                "methods": {
                    "oauth": {
                        "mode": "runtime_confirmed",
                        "target_model": "openai-codex/gpt-5.4",
                    }
                }
            },
        },
    }

    verify_manifest(manifest)


def test_verify_dashboard_release_rejects_wrong_codex_target_when_runtime_confirmed():
    manifest = {
        "manifest_type": "aichain.catalog",
        "public_artifact_readiness": {"dashboard_switch_ready": True},
        "canonical_public_artifact": {"migration_state": "safe_to_switch_dashboard_to_canonical_artifact"},
        "provider_access_matrix": {
            "openai": {"methods": {"api_key": {"mode": "runtime_confirmed"}}},
            "openrouter": {"methods": {"api_key": {"mode": "runtime_confirmed"}}},
            "deepseek": {"methods": {"api_key": {"mode": "runtime_confirmed"}}},
            "openai-codex": {
                "methods": {
                    "oauth": {
                        "mode": "runtime_confirmed",
                        "target_model": "openai-codex/gpt-5.3-codex",
                    }
                }
            },
        },
    }

    with pytest.raises(VerificationError, match="gpt-5.4"):
        verify_manifest(manifest)
