#!/usr/bin/env python3
"""Tests for the site/feed catalog contract between GitHub Pages and aichaind."""

from aichaind.routing.catalog_contract import validate_catalog_manifest


def _legacy_table() -> dict:
    return {
        "system_status": "OPERATIONAL",
        "version": "4.0-sovereign",
        "heavy_hitter": {"model": "openai/o3-pro"},
        "routing_hierarchy": [
            {
                "model": "google/gemini-2.5-flash",
                "provider": "Google",
                "tier": "FREE_FRONTIER",
                "metrics": {
                    "intelligence": 82,
                    "speed": 95,
                    "stability": 90,
                    "cost": 0.0,
                },
            },
            {
                "model": "openai/o3-pro",
                "provider": "OpenAI",
                "tier": "HEAVY_HITTER",
                "metrics": {
                    "intelligence": 99,
                    "speed": 85,
                    "stability": 96,
                    "cost": 0.01,
                },
            },
            {
                "model": "openai/gpt-4o",
                "provider": "OpenAI",
                "tier": "OAUTH_BRIDGE",
                "metrics": {
                    "intelligence": 92,
                    "speed": 88,
                    "stability": 95,
                    "cost": 0.0,
                },
            },
        ],
    }


class TestCatalogContract:
    def test_legacy_v4_contract_is_accepted_with_derived_roles(self):
        result = validate_catalog_manifest(_legacy_table())
        assert result.valid is True
        assert result.compat_mode == "legacy_v4"
        assert result.roles["fast"] == "google/gemini-2.5-flash"
        assert result.roles["heavy"] == "openai/o3-pro"
        assert result.roles["visual"] == "openai/gpt-4o"

    def test_v5_contract_is_accepted_in_native_mode(self):
        manifest = {
            "schema_version": "5.0.0",
            "manifest_type": "aichain.catalog",
            "system_status": "OPERATIONAL",
            "planes": {
                "global": {"kind": "catalog", "feed_url": "https://example.test/feed.json"},
                "local": {"kind": "execution", "skill": "openclaw", "sidecar": "aichaind"},
            },
            "roles": {
                "fast": {"model": "google/gemini-2.5-flash"},
                "heavy": {"model": "openai/o3-pro"},
                "visual": {"model": "openai/gpt-4o"},
            },
            "capabilities": {"supports_a2a": False},
            "routing_hierarchy": _legacy_table()["routing_hierarchy"],
        }
        result = validate_catalog_manifest(manifest)
        assert result.valid is True
        assert result.compat_mode == "native_v5"
        assert result.roles["heavy"] == "openai/o3-pro"

    def test_future_major_version_is_rejected(self):
        manifest = _legacy_table()
        manifest["schema_version"] = "6.0.0"
        result = validate_catalog_manifest(manifest)
        assert result.valid is False
        assert any("unsupported future schema version" in issue for issue in result.issues)

    def test_v5_manifest_requires_catalog_execution_plane_split(self):
        manifest = {
            "schema_version": "5.0.0",
            "manifest_type": "aichain.catalog",
            "roles": {
                "fast": {"model": "google/gemini-2.5-flash"},
                "heavy": {"model": "openai/o3-pro"},
                "visual": {"model": "openai/gpt-4o"},
            },
            "routing_hierarchy": _legacy_table()["routing_hierarchy"],
        }
        result = validate_catalog_manifest(manifest)
        assert result.valid is False
        assert "native_v5 manifest missing planes object" in result.issues

