#!/usr/bin/env python3
"""Tests for aichaind.providers.balance."""

import sys
import types
import pytest

from aichaind.providers.balance import BalanceChecker
from aichaind.providers.discovery import ProviderCredential


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class TestBalanceChecker:
    def test_openrouter_balance_api_and_cache(self, monkeypatch):
        calls = []

        def fake_get(url, headers=None, timeout=None):
            calls.append(url)
            return FakeResponse(200, {
                "data": {
                    "total_credits": 10,
                    "total_usage": 2,
                }
            })

        monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
        checker = BalanceChecker(cache_ttl=300)

        first = checker._check_one("openrouter", "or-key")
        second = checker._check_one("openrouter", "or-key")

        assert first.balance_usd == pytest.approx(8.0)
        assert first.has_credits is True
        assert second.source == "cached"
        assert len(calls) == 1

    def test_openai_project_key_detects_subscription(self):
        checker = BalanceChecker()
        bal = checker._check_one("openai", "sk-proj-123456")
        assert bal.is_subscription is True
        assert bal.has_credits is True
        assert bal.source == "estimated"

    def test_google_key_detects_free_tier(self):
        checker = BalanceChecker()
        bal = checker._check_one("google", "AIza-test-key")
        assert bal.is_free_tier is True
        assert bal.has_credits is True

    def test_check_all_populates_credit_lists(self):
        checker = BalanceChecker()
        credentials = [
            ProviderCredential(provider="openai", api_key="sk-proj-1"),
            ProviderCredential(provider="google", api_key="AIza-1"),
        ]

        report = checker.check_all(credentials)

        assert set(report.balances.keys()) == {"openai", "google"}
        assert "openai" in report.providers_with_credits
        assert "google" in report.providers_with_credits
        assert report.providers_empty == []
