#!/usr/bin/env python3
"""
aichaind.core.summarizer — Structured Context Summarizer

Compresses long conversation histories to fit within token budgets.
Uses extractive summarization (no LLM calls) for v1.
"""

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("aichaind.core.summarizer")

# Default thresholds
MAX_TURNS_BEFORE_SUMMARY = 20
MAX_CHARS_BEFORE_SUMMARY = 50_000
SUMMARY_TARGET_TURNS = 8


@dataclass
class SummaryResult:
    """Result of context compression."""
    compressed_messages: list[dict] = field(default_factory=list)
    original_turn_count: int = 0
    compressed_turn_count: int = 0
    chars_saved: int = 0
    pinned_facts: list[str] = field(default_factory=list)
    summary_text: str = ""


class ContextSummarizer:
    """
    Structured context compressor.

    Strategies (applied in order):
    1. Pin important facts (user-specified or auto-detected)
    2. Preserve system message and last N turns
    3. Summarize middle turns into a condensed context block
    4. Remove redundant assistant responses
    """

    def __init__(
        self,
        max_turns: int = MAX_TURNS_BEFORE_SUMMARY,
        max_chars: int = MAX_CHARS_BEFORE_SUMMARY,
        target_turns: int = SUMMARY_TARGET_TURNS,
    ):
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.target_turns = target_turns

    def needs_compression(self, messages: list[dict]) -> bool:
        """Check if the conversation needs compression."""
        if len(messages) > self.max_turns:
            return True
        total_chars = sum(
            len(str(m.get("content", ""))) for m in messages
        )
        return total_chars > self.max_chars

    def compress(
        self,
        messages: list[dict],
        pinned_facts: list[str] = None,
    ) -> SummaryResult:
        """
        Compress messages while preserving critical context.

        Returns a SummaryResult with compressed messages.
        """
        result = SummaryResult(original_turn_count=len(messages))

        if not self.needs_compression(messages):
            result.compressed_messages = messages
            result.compressed_turn_count = len(messages)
            return result

        # Separate system message
        system_msg = None
        conversation = []
        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg
            else:
                conversation.append(msg)

        # Keep last N turns (user+assistant pairs)
        keep_count = min(self.target_turns, len(conversation))
        recent = conversation[-keep_count:]
        middle = conversation[:-keep_count] if keep_count < len(conversation) else []

        # Extract key facts from middle section
        extracted_facts = self._extract_facts(middle)
        all_facts = list(set((pinned_facts or []) + extracted_facts))

        # Build summary of middle section
        summary_text = self._build_summary(middle, all_facts)

        # Assemble compressed messages
        compressed = []
        if system_msg:
            compressed.append(system_msg)

        if summary_text:
            compressed.append({
                "role": "system",
                "content": f"[Context Summary — {len(middle)} earlier messages compressed]\n{summary_text}",
            })

        compressed.extend(recent)

        orig_chars = sum(len(str(m.get("content", ""))) for m in messages)
        new_chars = sum(len(str(m.get("content", ""))) for m in compressed)

        result.compressed_messages = compressed
        result.compressed_turn_count = len(compressed)
        result.chars_saved = orig_chars - new_chars
        result.pinned_facts = all_facts
        result.summary_text = summary_text

        log.info(f"Compressed {len(messages)} → {len(compressed)} messages "
                 f"({result.chars_saved} chars saved)")

        return result

    def _extract_facts(self, messages: list[dict]) -> list[str]:
        """Extract key facts from messages using heuristics."""
        facts = []
        for msg in messages:
            content = str(msg.get("content", ""))
            if msg.get("role") == "user":
                # Extract questions and requirements
                for line in content.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # Keep lines that look like requirements or key info
                    if any(kw in line.lower() for kw in (
                        "must", "require", "important", "always", "never",
                        "constraint", "rule", "prefer", "config", "setting",
                    )):
                        facts.append(line[:200])
                    elif line.endswith("?") and len(line) > 20:
                        facts.append(f"Asked: {line[:150]}")

            elif msg.get("role") == "assistant":
                # Extract conclusions, decisions, actions
                for line in content.split("\n"):
                    line = line.strip()
                    if any(kw in line.lower() for kw in (
                        "conclusion", "decided", "solution", "answer:",
                        "result:", "found:", "created", "updated", "deleted",
                    )):
                        facts.append(line[:200])

        # Dedupe and limit
        seen = set()
        unique_facts = []
        for f in facts:
            normalized = f.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                unique_facts.append(f)
        return unique_facts[:20]  # Max 20 facts

    def _build_summary(self, messages: list[dict], facts: list[str]) -> str:
        """Build a summary block from older messages and facts."""
        if not messages and not facts:
            return ""

        parts = []

        if facts:
            parts.append("Key context from earlier conversation:")
            for fact in facts[:15]:
                parts.append(f"• {fact}")

        # Add topic overview
        topics = self._detect_topics(messages)
        if topics:
            parts.append(f"\nTopics discussed: {', '.join(topics)}")

        # Count by role
        user_msgs = sum(1 for m in messages if m.get("role") == "user")
        asst_msgs = sum(1 for m in messages if m.get("role") == "assistant")
        parts.append(f"\n({user_msgs} user messages, {asst_msgs} assistant responses compressed)")

        return "\n".join(parts)

    def _detect_topics(self, messages: list[dict]) -> list[str]:
        """Detect main topics from messages using simple frequency analysis."""
        # Topic keywords to look for
        topic_patterns = {
            "code": r"\b(code|function|class|variable|error|debug|refactor)\b",
            "data": r"\b(data|database|SQL|query|table|schema)\b",
            "API": r"\b(API|endpoint|REST|request|response|HTTP)\b",
            "security": r"\b(security|auth|token|encrypt|password|vulnerability)\b",
            "AI/ML": r"\b(model|training|inference|neural|embedding|LLM|GPT)\b",
            "config": r"\b(config|setting|environment|variable|parameter)\b",
            "deploy": r"\b(deploy|docker|kubernetes|CI|CD|pipeline|server)\b",
        }

        text = " ".join(str(m.get("content", "")) for m in messages)
        detected = []
        for topic, pattern in topic_patterns.items():
            if re.search(pattern, text, re.IGNORECASE):
                detected.append(topic)

        return detected[:5]
