#!/usr/bin/env python3
"""
Tests for aichaind.core.summarizer — Structured Context Summarizer

Covers:
- Compression detection threshold
- Extractive fact extraction
- Topic detection
- Context compression
- Pinned facts preservation
"""

import pytest
from aichaind.core.summarizer import ContextSummarizer, SummaryResult


@pytest.fixture
def summarizer():
    return ContextSummarizer(max_turns=5, max_chars=500, target_turns=3)


class TestNeedsCompression:
    def test_short_conversation_no_compression(self, summarizer):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        assert summarizer.needs_compression(msgs) is False

    def test_long_conversation_needs_compression(self, summarizer):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
        assert summarizer.needs_compression(msgs) is True

    def test_large_content_needs_compression(self, summarizer):
        msgs = [{"role": "user", "content": "x" * 600}]
        assert summarizer.needs_compression(msgs) is True


class TestCompression:
    def test_keeps_system_message(self, summarizer):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            *[{"role": "user", "content": f"question {i}"} for i in range(6)],
            *[{"role": "assistant", "content": f"answer {i}"} for i in range(6)],
        ]
        result = summarizer.compress(msgs)
        assert result.compressed_turn_count < result.original_turn_count
        assert result.compressed_messages[0]["role"] == "system"
        assert result.compressed_messages[0]["content"] == "You are helpful"

    def test_preserves_recent_messages(self, summarizer):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
        result = summarizer.compress(msgs)
        # Last 3 should be preserved
        last_contents = [m["content"] for m in result.compressed_messages if m["role"] == "user"]
        assert "msg 9" in last_contents[-1] or any("msg 9" in c for c in last_contents)

    def test_chars_saved_positive(self, summarizer):
        msgs = [{"role": "user", "content": f"very long message {i} " * 10} for i in range(10)]
        result = summarizer.compress(msgs)
        assert result.chars_saved > 0

    def test_no_compression_passthrough(self, summarizer):
        msgs = [{"role": "user", "content": "hi"}]
        result = summarizer.compress(msgs)
        assert result.compressed_messages == msgs
        assert result.chars_saved == 0

    def test_pinned_facts_included(self, summarizer):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
        result = summarizer.compress(msgs, pinned_facts=["important_fact_1"])
        assert "important_fact_1" in result.pinned_facts


class TestFactExtraction:
    def test_extracts_requirements(self):
        s = ContextSummarizer()
        msgs = [{"role": "user", "content": "You must always use UTC timestamps. The constraint is 100ms max latency."}]
        facts = s._extract_facts(msgs)
        assert len(facts) > 0

    def test_extracts_conclusions(self):
        s = ContextSummarizer()
        msgs = [{"role": "assistant", "content": "Conclusion: the best approach is to use async I/O."}]
        facts = s._extract_facts(msgs)
        assert len(facts) > 0


class TestTopicDetection:
    def test_detects_code_topic(self):
        s = ContextSummarizer()
        msgs = [{"role": "user", "content": "I need to refactor this function and fix the error"}]
        topics = s._detect_topics(msgs)
        assert "code" in topics

    def test_detects_api_topic(self):
        s = ContextSummarizer()
        msgs = [{"role": "user", "content": "The REST API endpoint returns a 404 response"}]
        topics = s._detect_topics(msgs)
        assert "API" in topics

    def test_detects_security_topic(self):
        s = ContextSummarizer()
        msgs = [{"role": "user", "content": "Check the auth token and password encryption"}]
        topics = s._detect_topics(msgs)
        assert "security" in topics
