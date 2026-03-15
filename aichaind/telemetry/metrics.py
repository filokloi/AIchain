#!/usr/bin/env python3
"""
aichaind.telemetry.metrics — Operator Metrics Registry

A lightweight, in-memory metrics registry designed for operators.
Exposes decision-grade rolling metrics (total requests, fallback counts,
route allocations, and moving average latencies) without the overhead
of heavy analytics databases.
"""

import threading
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger("aichaind.telemetry.metrics")

class OperatorMetrics:
    """Thread-safe, lightweight metrics registry for OpenClaw telemetry."""

    def __init__(self, data_dir: Path):
        self.lock = threading.Lock()
        self.metrics_file = data_dir / "metrics.json"

        # Counters
        self.total_requests = 0
        self.fallback_events = 0
        self.manual_overrides = 0
        self.quota_demotions = 0
        self.timeout_events = 0

        # Distribution Maps
        self.routes_selected = {}

        # Latency (Simple EWMA: Exponentially Weighted Moving Average)
        # alpha = 0.1 -> Gives ~ 10 request window weighting
        self.ewma_latency_ms = 0.0
        self.alpha = 0.1

        # Try to load existing snapshot to maintain continuity across daemon restarts
        self._load_snapshot()

    def record_request(self, model_id: str, is_manual: bool = False,
                       is_fallback: bool = False, latency_ms: float = 0.0):
        with self.lock:
            self.total_requests += 1

            # Route counting
            if model_id not in self.routes_selected:
                self.routes_selected[model_id] = 0
            self.routes_selected[model_id] += 1

            if is_fallback:
                self.fallback_events += 1

            if is_manual:
                self.manual_overrides += 1

            # Latency Moving Average
            if self.total_requests == 1 or self.ewma_latency_ms == 0.0:
                self.ewma_latency_ms = latency_ms
            else:
                self.ewma_latency_ms = (self.alpha * latency_ms) + ((1.0 - self.alpha) * self.ewma_latency_ms)

    def record_demotion(self):
        with self.lock:
            self.quota_demotions += 1

    def record_timeout(self):
        with self.lock:
            self.timeout_events += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "total_requests": self.total_requests,
                "fallback_events": self.fallback_events,
                "manual_overrides": self.manual_overrides,
                "quota_demotions": self.quota_demotions,
                "timeout_events": self.timeout_events,
                "average_latency_ms": round(self.ewma_latency_ms, 2),
                "routes_selected": dict(self.routes_selected)
            }

    def _load_snapshot(self):
        if not self.metrics_file.exists():
            return
        try:
            with open(self.metrics_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.total_requests = data.get("total_requests", 0)
            self.fallback_events = data.get("fallback_events", 0)
            self.manual_overrides = data.get("manual_overrides", 0)
            self.quota_demotions = data.get("quota_demotions", 0)
            self.timeout_events = data.get("timeout_events", 0)
            self.ewma_latency_ms = data.get("average_latency_ms", 0.0)
            self.routes_selected = data.get("routes_selected", {})
            log.info(f"Loaded existing metrics snapshot (Requests: {self.total_requests})")
        except Exception as e:
            log.warning(f"Failed to load metrics snapshot: {e}")

    def flush_snapshot(self):
        """Called periodically or on shutdown to preserve rolling metrics."""
        try:
            self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.metrics_file, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, indent=2)
        except Exception as e:
            log.error(f"Failed to flush metrics snapshot: {e}")
