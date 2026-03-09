#!/usr/bin/env python3
"""Tests for v2.0 modules: encoder, cloud_classifier, route_eval, msgpack_codec."""

import pytest
import tempfile
from pathlib import Path

# ── Layer 3: Encoder ──
from aichaind.routing.encoder import LocalEncoderScorer, _tokenize, _cosine_similarity


class TestTokenize:
    def test_basic(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_punctuation(self):
        assert _tokenize("What's up? 123") == ["what", "s", "up", "123"]

    def test_empty(self):
        assert _tokenize("") == []


class TestEncoderScorer:
    @pytest.fixture
    def scorer(self):
        return LocalEncoderScorer()

    def test_heavy_reasoning(self, scorer):
        msgs = [{"role": "user", "content": "prove theorem using mathematical induction and derive the lemma"}]
        r = scorer.score(msgs)
        assert r is not None
        assert r.category == "heavy_reasoning"
        assert r.model_preference == "heavy"

    def test_heavy_code(self, scorer):
        msgs = [{"role": "user", "content": "implement REST API endpoint with database migration and unit test"}]
        r = scorer.score(msgs)
        assert r is not None
        assert r.model_preference == "heavy"

    def test_free_simple(self, scorer):
        msgs = [{"role": "user", "content": "what is the capital define explain simply"}]
        r = scorer.score(msgs)
        assert r is not None
        assert r.model_preference == "free"

    def test_visual(self, scorer):
        msgs = [{"role": "user", "content": "analyze this image screenshot UI design mockup"}]
        r = scorer.score(msgs)
        assert r is not None
        assert r.model_preference == "visual"

    def test_short_text_returns_none(self, scorer):
        msgs = [{"role": "user", "content": "hi"}]
        r = scorer.score(msgs)
        assert r is None

    def test_scores_dict_populated(self, scorer):
        msgs = [{"role": "user", "content": "write a function to sort an array using dynamic programming"}]
        r = scorer.score(msgs)
        assert r is not None
        assert len(r.scores) == 5  # all categories scored

    def test_cascade_routes_code_to_heavy(self):
        """Code queries with many code keywords should route heavy via L2 or L3."""
        from aichaind.routing.cascade import CascadeRouter
        cr = CascadeRouter({"layer3_enabled": True, "layer4_enabled": False})
        d = cr.route(
            messages=[{"role": "user", "content": "implement a function with unit test integration test mock fixture using design pattern factory singleton observer for REST API endpoint"}],
            available_free_model="free/m",
            available_heavy_model="heavy/m",
        )
        assert d.target_model == "heavy/m"


# ── Layer 4: Cloud Classifier ──
from aichaind.routing.cloud_classifier import CloudClassifier


class TestCloudClassifier:
    def test_parse_valid_json(self):
        cc = CloudClassifier()
        r = cc._parse_response('{"category": "HEAVY_CODE", "confidence": 0.95}', 100.0, "test/model")
        assert r is not None
        assert r.category == "HEAVY_CODE"
        assert r.model_preference == "heavy"
        assert r.confidence == 0.95

    def test_parse_code_fenced(self):
        cc = CloudClassifier()
        r = cc._parse_response('```json\n{"category": "FREE_SIMPLE", "confidence": 0.8}\n```', 50, "m")
        assert r is not None
        assert r.category == "FREE_SIMPLE"

    def test_parse_invalid_category(self):
        cc = CloudClassifier()
        r = cc._parse_response('{"category": "UNKNOWN", "confidence": 0.9}', 50, "m")
        assert r is None

    def test_parse_bad_json(self):
        cc = CloudClassifier()
        r = cc._parse_response("not json at all", 50, "m")
        assert r is None

    def test_classify_no_adapter_returns_none(self):
        cc = CloudClassifier()
        r = cc.classify([{"role": "user", "content": "hello"}])
        assert r is None

    def test_confidence_capped(self):
        cc = CloudClassifier()
        r = cc._parse_response('{"category": "HEAVY_CODE", "confidence": 1.5}', 50, "m")
        assert r.confidence == 0.98


# ── Route Eval Collector ──
from aichaind.telemetry.route_eval import RouteEvalCollector, RouteEvalRecord


class TestRouteEvalCollector:
    def test_record_and_count(self, tmp_path):
        ec = RouteEvalCollector(tmp_path / "eval.jsonl")
        ec.record(RouteEvalRecord(query_hash="abc", final_model="test/m"))
        assert ec.count == 1

    def test_load_all(self, tmp_path):
        ec = RouteEvalCollector(tmp_path / "eval.jsonl")
        ec.record(RouteEvalRecord(query_hash="a", final_model="m1"))
        ec.record(RouteEvalRecord(query_hash="b", final_model="m2"))
        records = ec.load_all()
        assert len(records) == 2

    def test_stats(self, tmp_path):
        ec = RouteEvalCollector(tmp_path / "eval.jsonl")
        ec.record(RouteEvalRecord(query_hash="a", final_model="m1", exec_status="success"))
        ec.record(RouteEvalRecord(query_hash="b", final_model="m1", exec_status="error"))
        s = ec.stats()
        assert s["total"] == 2
        assert "success" in s["statuses"]

    def test_empty_stats(self, tmp_path):
        ec = RouteEvalCollector(tmp_path / "eval.jsonl")
        s = ec.stats()
        assert s["total"] == 0


# ── MessagePack Codec ──
from aichaind.transport.msgpack_codec import encode, decode, benchmark, MSGPACK_AVAILABLE


class TestMsgPackCodec:
    def test_json_encode_decode(self):
        data = {"key": "value", "num": 42}
        raw = encode(data, use_msgpack=False)
        result = decode(raw, use_msgpack=False)
        assert result == data

    def test_roundtrip(self):
        data = {"choices": [{"message": {"content": "hello"}}], "usage": {"tokens": 10}}
        raw = encode(data, use_msgpack=MSGPACK_AVAILABLE)
        result = decode(raw, use_msgpack=MSGPACK_AVAILABLE)
        assert result == data

    def test_benchmark_runs(self):
        result = benchmark(iterations=10)
        assert "json" in result
        assert result["json"]["size_bytes"] > 0

    def test_unicode_data(self):
        data = {"text": "Zdravo svete! Ovo je test."}
        raw = encode(data, use_msgpack=False)
        result = decode(raw, use_msgpack=False)
        assert result["text"] == data["text"]
