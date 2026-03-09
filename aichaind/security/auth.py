#!/usr/bin/env python3
"""
aichaind.security.auth — Per-startup token authentication.

Generates a random 256-bit token on daemon start.
Token is written to a restricted file that the OpenClaw skill reads.
Every IPC request must present this token.
"""

import os
import secrets
import time
import logging
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger("aichaind.security.auth")


# ─────────────────────────────────────────
# TOKEN MANAGEMENT
# ─────────────────────────────────────────

class AuthTokenManager:
    """
    Per-startup token authentication.

    Flow:
        1. aichaind starts → generate_token() → writes to .auth_token file
        2. OpenClaw skill reads .auth_token → includes in every IPC request
        3. aichaind validates on every request → reject if mismatch
    """

    def __init__(self, token_path: Path):
        self.token_path = token_path
        self._current_token: str = ""
        self._created_at: float = 0.0
        self._failed_attempts: int = 0
        self._lockout_until: float = 0.0

        # Rate limiting config
        self.max_failed_attempts: int = 20
        self.lockout_duration_seconds: float = 10.0

    def generate_token(self) -> str:
        """Generate a new per-startup auth token and write to file."""
        self._current_token = secrets.token_urlsafe(32)  # 256-bit
        self._created_at = time.time()
        self._failed_attempts = 0
        self._lockout_until = 0.0

        # Write token to file
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(self._current_token, encoding="utf-8")

        # Restrict file permissions (best-effort on Windows)
        try:
            os.chmod(str(self.token_path), 0o600)
        except OSError:
            # Windows may not support Unix permissions — acceptable
            pass

        log.info(f"Auth token generated at {self.token_path}")
        return self._current_token

    def validate(self, token: str) -> bool:
        """
        Validate a token against the current startup token.
        Returns True if valid, False otherwise.
        Implements rate limiting on failed attempts.
        """
        # Check lockout
        if self._lockout_until > 0 and time.time() < self._lockout_until:
            remaining = self._lockout_until - time.time()
            log.warning(f"Auth locked out for {remaining:.0f}s more")
            return False

        if not self._current_token:
            log.error("No auth token set — rejecting request")
            return False

        if secrets.compare_digest(token, self._current_token):
            # Success — reset failure counter
            self._failed_attempts = 0
            return True

        # Failed attempt
        self._failed_attempts += 1
        log.warning(f"Auth failed ({self._failed_attempts}/{self.max_failed_attempts})")

        if self._failed_attempts >= self.max_failed_attempts:
            self._lockout_until = time.time() + self.lockout_duration_seconds
            log.warning(f"Auth LOCKOUT for {self.lockout_duration_seconds}s")

        return False

    def revoke(self):
        """Revoke current token and remove file."""
        self._current_token = ""
        if self.token_path.exists():
            self.token_path.unlink()
        log.info("Auth token revoked")

    @property
    def is_active(self) -> bool:
        return bool(self._current_token)

    @property
    def is_locked_out(self) -> bool:
        return self._lockout_until > 0 and time.time() < self._lockout_until


# ─────────────────────────────────────────
# ORIGIN VALIDATION
# ─────────────────────────────────────────

# Allowlisted origins for WS/HTTP connections to aichaind
ALLOWED_ORIGINS = frozenset([
    "http://localhost",
    "http://127.0.0.1",
    "https://localhost",
    "https://127.0.0.1",
    "http://[::1]",
])


def validate_origin(origin: str) -> bool:
    """
    Validate that the request origin is from localhost.
    Strips port numbers before matching.
    """
    if not origin:
        # No origin header — could be non-browser, allow with caution
        return True

    # Strip port
    origin_base = origin.rstrip("/")
    # Remove port number for comparison
    for allowed in ALLOWED_ORIGINS:
        if origin_base == allowed or origin_base.startswith(allowed + ":"):
            return True

    log.warning(f"Origin rejected: {origin}")
    return False
