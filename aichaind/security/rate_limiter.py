#!/usr/bin/env python3
"""
aichaind.security.rate_limiter — Token Bucket Rate Limiter

Protects aichaind endpoints from excessive requests.
Default: 60 req/min with burst capacity.
"""

import time
import logging
from collections import defaultdict

log = logging.getLogger("aichaind.security.rate_limiter")


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter.
    Each client (identified by IP or origin) gets its own bucket.
    """

    def __init__(self, rate: float = 60.0, burst: int = 10):
        """
        Args:
            rate: tokens per minute (sustained rate)
            burst: max token capacity (burst size)
        """
        self.rate_per_second = rate / 60.0
        self.burst = burst
        self._buckets: dict[str, dict] = defaultdict(lambda: {
            "tokens": burst,
            "last_refill": time.time(),
        })

    def allow(self, client_id: str = "default") -> bool:
        """Check if request is allowed. Returns True if allowed."""
        bucket = self._buckets[client_id]
        now = time.time()

        # Refill tokens based on elapsed time
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            self.burst,
            bucket["tokens"] + elapsed * self.rate_per_second
        )
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True

        log.warning(f"Rate limit hit for client: {client_id}")
        return False

    def remaining(self, client_id: str = "default") -> float:
        """Return remaining tokens for a client."""
        if client_id not in self._buckets:
            return float(self.burst)
        return self._buckets[client_id]["tokens"]

    def reset(self, client_id: str = "default"):
        """Reset a client's bucket."""
        self._buckets[client_id] = {
            "tokens": self.burst,
            "last_refill": time.time(),
        }
