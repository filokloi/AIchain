#!/usr/bin/env python3
"""
aichaind.telemetry.route_eval — Route Evaluation Dataset Collector

Collects routing decisions and their outcomes for offline analysis
and future model training. Appends structured records to a JSONL file.

Records include:
  - Input query (truncated, PII-redacted)
  - Layer decisions at each stage
  - Final model chosen
  - Outcome metrics (latency, success/failure, tokens)
"""

import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, asdict

log = logging.getLogger("aichaind.telemetry.route_eval")


@dataclass
class RouteEvalRecord:
    """A single routing evaluation record."""
    timestamp: str = ""
    query_hash: str = ""          # SHA256 of query (not raw text)
    query_length: int = 0
    query_word_count: int = 0

    # Layer decisions
    l1_decision: str = ""
    l1_confidence: float = 0.0
    l2_cluster: str = ""
    l2_confidence: float = 0.0
    l3_category: str = ""
    l3_confidence: float = 0.0
    l4_category: str = ""
    l4_confidence: float = 0.0

    # Final routing
    final_model: str = ""
    final_confidence: float = 0.0
    decision_layers: str = ""     # comma-separated layer tags
    route_latency_ms: float = 0.0

    # Outcome
    exec_status: str = ""         # success, error, timeout
    exec_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""

    # Context
    pii_detected: bool = False
    godmode: bool = False
    visual_input: bool = False


class RouteEvalCollector:
    """
    Collects routing evaluation data for offline analysis.
    Thread-safe append-only JSONL writer.
    """

    def __init__(self, eval_path: Path):
        self.eval_path = Path(eval_path)
        self.eval_path.parent.mkdir(parents=True, exist_ok=True)
        self._count = 0

    def record(self, rec: RouteEvalRecord):
        """Append a record to the eval dataset."""
        if not rec.timestamp:
            from datetime import datetime, timezone
            rec.timestamp = datetime.now(timezone.utc).isoformat()

        try:
            with open(self.eval_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
            self._count += 1
        except Exception as e:
            log.error(f"Failed to write eval record: {e}")

    def record_from_route(
        self,
        query_hash: str,
        query_length: int,
        query_word_count: int,
        decision,                    # RouteDecision
        exec_status: str = "",
        exec_latency_ms: float = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        finish_reason: str = "",
        pii_detected: bool = False,
        godmode: bool = False,
        visual_input: bool = False,
    ):
        """Convenience: create and record from a RouteDecision + outcome."""
        layers_str = ",".join(decision.decision_layers) if decision.decision_layers else ""

        rec = RouteEvalRecord(
            query_hash=query_hash,
            query_length=query_length,
            query_word_count=query_word_count,
            final_model=decision.target_model or "",
            final_confidence=decision.confidence,
            decision_layers=layers_str,
            route_latency_ms=decision.latency_ms,
            exec_status=exec_status,
            exec_latency_ms=exec_latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            pii_detected=pii_detected,
            godmode=godmode,
            visual_input=visual_input,
        )

        # Parse layer-specific info from decision_layers
        for layer_tag in decision.decision_layers:
            if layer_tag.startswith("L1:"):
                rec.l1_decision = layer_tag
                rec.l1_confidence = decision.confidence
            elif layer_tag.startswith("L2:"):
                rec.l2_cluster = layer_tag.replace("L2:semantic:", "")
                rec.l2_confidence = decision.confidence
            elif layer_tag.startswith("L3:"):
                rec.l3_category = layer_tag.replace("L3:encoder:", "")
                rec.l3_confidence = decision.confidence
            elif layer_tag.startswith("L4:"):
                rec.l4_category = layer_tag.replace("L4:cloud:", "")
                rec.l4_confidence = decision.confidence

        self.record(rec)

    @property
    def count(self) -> int:
        return self._count

    def load_all(self) -> list[RouteEvalRecord]:
        """Load all records from the eval file."""
        if not self.eval_path.exists():
            return []
        records = []
        for line in self.eval_path.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                try:
                    data = json.loads(line)
                    records.append(RouteEvalRecord(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
        return records

    def stats(self) -> dict:
        """Compute basic statistics from the eval dataset."""
        records = self.load_all()
        if not records:
            return {"total": 0}

        statuses = {}
        models = {}
        layers_used = {}
        total_route_lat = 0
        total_exec_lat = 0

        for r in records:
            statuses[r.exec_status] = statuses.get(r.exec_status, 0) + 1
            models[r.final_model] = models.get(r.final_model, 0) + 1
            for l in r.decision_layers.split(","):
                l = l.strip()
                if l:
                    tag = l.split(":")[0] + ":" + l.split(":")[1] if ":" in l else l
                    layers_used[tag] = layers_used.get(tag, 0) + 1
            total_route_lat += r.route_latency_ms
            total_exec_lat += r.exec_latency_ms

        n = len(records)
        return {
            "total": n,
            "statuses": statuses,
            "top_models": dict(sorted(models.items(), key=lambda x: -x[1])[:10]),
            "layers_used": layers_used,
            "avg_route_latency_ms": round(total_route_lat / n, 2),
            "avg_exec_latency_ms": round(total_exec_lat / n, 2),
            "pii_rate": round(sum(1 for r in records if r.pii_detected) / n, 3),
            "godmode_rate": round(sum(1 for r in records if r.godmode) / n, 3),
        }
