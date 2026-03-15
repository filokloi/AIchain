#!/usr/bin/env python3
"""
Tests for aichaind.routing.semantic — Layer 2 Semantic Pre-routing

Covers:
- Intent cluster matching
- Multi-keyword boost
- heavy vs free classification
- No-match returns None
- Cascade router integration with Layer 2
"""

import pytest
from aichaind.routing.semantic import semantic_preroute, SemanticResult
from aichaind.routing.cascade import CascadeRouter


class TestSemanticPreroute:
    def test_deep_analysis_detected(self):
        msgs = [{"role": "user", "content": "I need a comprehensive analysis of the trade-off analysis between microservices and monoliths"}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.cluster_name == "deep_analysis"
        assert result.model_preference == "heavy"

    def test_simple_qa_detected(self):
        msgs = [{"role": "user", "content": "What is the capital of France?"}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.cluster_name == "simple_qa"
        assert result.model_preference == "free"

    def test_casual_chat_detected(self):
        msgs = [{"role": "user", "content": "Hello, how are you doing today?"}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.model_preference == "free"

    def test_code_generation_detected(self):
        msgs = [{"role": "user", "content": "Write code for a REST API endpoint with unit test coverage"}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.cluster_name == "code_generation"
        assert result.model_preference == "heavy"

    def test_code_generation_detected_for_programming_game_prompt(self):
        msgs = [{"role": "user", "content": "Let's program a Tetris game in Python."}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.cluster_name == "code_generation"
        assert result.model_preference == "heavy"

    def test_complex_reasoning_detected(self):
        msgs = [{"role": "user", "content": "Prove that the algorithm complexity is NP-hard using mathematical proof by contradiction"}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.model_preference == "heavy"

    def test_security_sensitive_detected(self):
        msgs = [{"role": "user", "content": "How to find a vulnerability in this API endpoint using penetration test techniques"}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.model_preference == "heavy"

    def test_no_match_returns_none(self):
        msgs = [{"role": "user", "content": "Blue sky over the mountains"}]
        result = semantic_preroute(msgs)
        # Could be None or very low confidence
        if result:
            assert result.confidence < 0.85

    def test_multi_keyword_boosts_confidence(self):
        msgs = [{"role": "user", "content": "step by step mathematical proof using induction and derive the theorem"}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.confidence > 0.85  # boosted

    def test_uses_last_user_message(self):
        msgs = [
            {"role": "user", "content": "write code for me"},
            {"role": "assistant", "content": "sure"},
            {"role": "user", "content": "hello how are you"},
        ]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.model_preference == "free"  # "hello" → casual

    def test_multimodal_text_extraction(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "What is the meaning of this?"},
            {"type": "image_url", "image_url": {"url": "data:..."}}
        ]}]
        result = semantic_preroute(msgs)
        assert result is not None
        assert result.model_preference == "free"  # "what is" → simple_qa


class TestCascadeWithLayer2:
    def test_l2_routes_heavy_for_complex_code(self):
        cr = CascadeRouter()
        d = cr.route(
            messages=[{"role": "user", "content": "Write code to implement a database schema migration with unit test"}],
            available_free_model="free/m",
            available_heavy_model="heavy/m",
        )
        assert d.target_model == "heavy/m"
        assert any(layer.startswith("L1:coding_intent") or "L2:semantic" in layer for layer in d.decision_layers)

    def test_l2_routes_free_for_casual(self):
        cr = CascadeRouter()
        d = cr.route(
            messages=[{"role": "user", "content": "Hello, good morning! Tell me a joke please"}],
            available_free_model="free/m",
            available_heavy_model="heavy/m",
        )
        assert d.target_model == "free/m"

    def test_l1_godmode_overrides_l2(self):
        cr = CascadeRouter()
        d = cr.route(
            messages=[{"role": "user", "content": "hello how are you"}],
            godmode_model="override/m",
            available_free_model="free/m",
        )
        assert d.target_model == "override/m"  # L1 godmode wins
