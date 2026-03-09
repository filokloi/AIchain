#!/usr/bin/env python3
"""
Tests for aichaind.core.policy — PolicyEngine

Covers:
- Model blacklisting
- Provider blacklisting
- PII blocks cloud routing
- Budget enforcement (exhausted, warning)
- Default permissive behavior
"""

import pytest
from aichaind.core.policy import PolicyEngine, PolicyResult


class TestPolicyEngine:
    def test_default_allows_everything(self):
        pe = PolicyEngine()
        result = pe.evaluate(target_model="any/model")
        assert result.allowed is True
        assert result.block_cloud is False

    def test_model_blacklist(self):
        pe = PolicyEngine({"model_blacklist": ["bad/model", "evil/model"]})
        r = pe.evaluate(target_model="bad/model")
        assert r.allowed is False
        assert "blacklisted" in r.reason

    def test_model_not_in_blacklist(self):
        pe = PolicyEngine({"model_blacklist": ["bad/model"]})
        r = pe.evaluate(target_model="good/model")
        assert r.allowed is True

    def test_provider_blacklist(self):
        pe = PolicyEngine({"provider_blacklist": ["sketchy_provider"]})
        r = pe.evaluate(target_provider="sketchy_provider")
        assert r.allowed is False

    def test_pii_blocks_cloud(self):
        pe = PolicyEngine({"pii_blocks_cloud": True})
        r = pe.evaluate(contains_pii=True)
        assert r.allowed is True
        assert r.block_cloud is True
        assert "pii" in r.reason.lower()

    def test_pii_no_block_when_disabled(self):
        pe = PolicyEngine({"pii_blocks_cloud": False})
        r = pe.evaluate(contains_pii=True)
        assert r.block_cloud is False

    def test_budget_exhausted(self):
        pe = PolicyEngine()
        r = pe.evaluate(budget_spent=10.0, budget_limit=10.0)
        assert r.allowed is True
        assert "budget_exhausted" in r.reason

    def test_budget_warning(self):
        pe = PolicyEngine()
        r = pe.evaluate(budget_spent=8.5, budget_limit=10.0)
        assert r.allowed is True
        assert "budget_warning" in r.reason

    def test_budget_ok(self):
        pe = PolicyEngine()
        r = pe.evaluate(budget_spent=2.0, budget_limit=10.0)
        assert r.allowed is True
        assert r.reason == ""
