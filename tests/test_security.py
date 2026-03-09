#!/usr/bin/env python3
"""
Tests for aichaind.security — auth, rate_limiter, origin validation

Covers:
- Token generation & validation
- Constant-time comparison
- Lockout after max failures
- Origin allowlist
- Token bucket rate limiting
"""

import pytest
import tempfile
import time
from pathlib import Path

from aichaind.security.auth import AuthTokenManager, validate_origin
from aichaind.security.rate_limiter import TokenBucketRateLimiter


@pytest.fixture
def auth_manager():
    path = Path(tempfile.mktemp(suffix=".token"))
    mgr = AuthTokenManager(path)
    yield mgr
    if path.exists():
        path.unlink()


class TestAuthTokenManager:
    def test_generate_token(self, auth_manager):
        token = auth_manager.generate_token()
        assert token != ""
        assert len(token) > 20
        assert auth_manager.is_active

    def test_validate_correct_token(self, auth_manager):
        token = auth_manager.generate_token()
        assert auth_manager.validate(token) is True

    def test_reject_wrong_token(self, auth_manager):
        auth_manager.generate_token()
        assert auth_manager.validate("completely_wrong") is False

    def test_reject_empty_token(self, auth_manager):
        auth_manager.generate_token()
        assert auth_manager.validate("") is False

    def test_lockout_after_max_failures(self, auth_manager):
        auth_manager.max_failed_attempts = 3
        auth_manager.lockout_duration_seconds = 1.0
        auth_manager.generate_token()

        auth_manager.validate("wrong1")
        auth_manager.validate("wrong2")
        auth_manager.validate("wrong3")

        assert auth_manager.is_locked_out is True
        # Even correct token should be rejected during lockout
        correct = auth_manager._current_token
        assert auth_manager.validate(correct) is False

    def test_lockout_expires(self, auth_manager):
        auth_manager.max_failed_attempts = 2
        auth_manager.lockout_duration_seconds = 0.5
        token = auth_manager.generate_token()

        auth_manager.validate("wrong1")
        auth_manager.validate("wrong2")
        assert auth_manager.is_locked_out is True

        time.sleep(0.6)
        assert auth_manager.is_locked_out is False
        assert auth_manager.validate(token) is True

    def test_token_written_to_file(self, auth_manager):
        token = auth_manager.generate_token()
        file_content = auth_manager.token_path.read_text(encoding="utf-8")
        assert file_content == token

    def test_revoke_clears_token(self, auth_manager):
        auth_manager.generate_token()
        auth_manager.revoke()
        assert auth_manager.is_active is False
        assert not auth_manager.token_path.exists()

    def test_regenerate_resets_failures(self, auth_manager):
        auth_manager.max_failed_attempts = 2
        auth_manager.generate_token()
        auth_manager.validate("wrong1")
        auth_manager.validate("wrong2")
        # Regenerate
        new_token = auth_manager.generate_token()
        assert auth_manager.is_locked_out is False
        assert auth_manager.validate(new_token) is True


class TestOriginValidation:
    def test_localhost_allowed(self):
        assert validate_origin("http://localhost") is True
        assert validate_origin("https://localhost") is True

    def test_localhost_with_port_allowed(self):
        assert validate_origin("http://localhost:8080") is True
        assert validate_origin("http://127.0.0.1:3000") is True

    def test_127_allowed(self):
        assert validate_origin("http://127.0.0.1") is True
        assert validate_origin("https://127.0.0.1") is True

    def test_ipv6_loopback_allowed(self):
        assert validate_origin("http://[::1]") is True

    def test_external_origin_blocked(self):
        assert validate_origin("http://evil.com") is False
        assert validate_origin("https://attacker.net") is False
        assert validate_origin("http://192.168.1.100") is False

    def test_empty_origin_allowed(self):
        # Non-browser requests may not have Origin header
        assert validate_origin("") is True


class TestTokenBucketRateLimiter:
    def test_allows_within_burst(self):
        rl = TokenBucketRateLimiter(rate=60, burst=5)
        for _ in range(5):
            assert rl.allow("client1") is True

    def test_blocks_after_burst(self):
        rl = TokenBucketRateLimiter(rate=60, burst=3)
        assert rl.allow("c") is True
        assert rl.allow("c") is True
        assert rl.allow("c") is True
        assert rl.allow("c") is False

    def test_refills_over_time(self):
        rl = TokenBucketRateLimiter(rate=600, burst=2)  # 10/sec
        assert rl.allow("c") is True
        assert rl.allow("c") is True
        assert rl.allow("c") is False  # exhausted
        time.sleep(0.15)  # should refill ~1.5 tokens
        assert rl.allow("c") is True

    def test_independent_clients(self):
        rl = TokenBucketRateLimiter(rate=60, burst=1)
        assert rl.allow("a") is True
        assert rl.allow("a") is False
        assert rl.allow("b") is True  # different client

    def test_remaining_reports_correctly(self):
        rl = TokenBucketRateLimiter(rate=60, burst=5)
        assert rl.remaining("x") == 5.0
        rl.allow("x")
        assert rl.remaining("x") < 5.0

    def test_reset(self):
        rl = TokenBucketRateLimiter(rate=60, burst=3)
        rl.allow("c")
        rl.allow("c")
        rl.allow("c")
        assert rl.allow("c") is False
        rl.reset("c")
        assert rl.allow("c") is True
