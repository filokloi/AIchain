#!/usr/bin/env python3
"""
aichaind.routing.semantic — Layer 2: Semantic Pre-routing

Keyword/topic-based pre-routing that runs in 1-5ms.
Uses static keyword clusters to classify intent without any ML model.
Designed to catch cases Layer 1 misses without incurring cloud latency.
"""

import re
import logging
from dataclasses import dataclass, field

log = logging.getLogger("aichaind.routing.semantic")


# ─────────────────────────────────────────
# INTENT CLUSTERS
# ─────────────────────────────────────────

@dataclass
class IntentCluster:
    """A cluster of keywords mapping to a routing intent."""
    name: str
    keywords: list[str]
    model_preference: str  # "heavy", "free", "visual", "specialist"
    confidence: float = 0.80
    description: str = ""


# Pre-compiled clusters — order matters (first match wins for ties)
INTENT_CLUSTERS = [
    # ── Heavy Brain indicators ──
    IntentCluster(
        name="deep_analysis",
        keywords=[
            "analyze in detail", "comprehensive analysis", "deep dive",
            "research paper", "literature review", "systematic review",
            "compare and contrast", "pros and cons", "trade-off analysis",
            "evaluate the impact", "root cause analysis", "post-mortem",
        ],
        model_preference="heavy",
        confidence=0.82,
    ),
    IntentCluster(
        name="complex_reasoning",
        keywords=[
            "step by step", "chain of thought", "prove that", "derive",
            "mathematical proof", "theorem", "induction", "contradiction",
            "logical fallacy", "deduction", "hypothesis", "axiom",
            "dynamic programming", "algorithm complexity", "NP-hard",
        ],
        model_preference="heavy",
        confidence=0.85,
    ),
    IntentCluster(
        name="creative_writing",
        keywords=[
            "write a story", "creative writing", "poem", "screenplay",
            "novel chapter", "character development", "world building",
            "narrative arc", "dialogue", "monologue", "essay",
        ],
        model_preference="heavy",
        confidence=0.78,
    ),
    IntentCluster(
        name="code_generation",
        keywords=[
            "write code", "implement", "refactor", "debug this",
            "unit test", "integration test", "API endpoint",
            "database schema", "migration", "deploy", "dockerfile",
            "CI/CD", "github actions", "webpack", "vite",
        ],
        model_preference="heavy",
        confidence=0.80,
    ),

    # ── Free Brain indicators ──
    IntentCluster(
        name="simple_qa",
        keywords=[
            "what is", "who is", "when did", "where is",
            "how many", "define", "meaning of", "explain simply",
            "translate", "convert", "calculate", "what time",
        ],
        model_preference="free",
        confidence=0.82,
    ),
    IntentCluster(
        name="casual_chat",
        keywords=[
            "hello", "hi", "hey", "thanks", "thank you",
            "good morning", "good night", "how are you",
            "tell me a joke", "fun fact", "random",
        ],
        model_preference="free",
        confidence=0.90,
    ),
    IntentCluster(
        name="formatting",
        keywords=[
            "format this", "rewrite this", "summarize",
            "bullet points", "table format", "markdown",
            "shorten this", "expand this", "paraphrase",
        ],
        model_preference="free",
        confidence=0.80,
    ),

    # ── Security/sensitive ──
    IntentCluster(
        name="security_sensitive",
        keywords=[
            "password", "credential", "secret key", "api key",
            "vulnerability", "exploit", "penetration test",
            "reverse shell", "payload", "injection",
        ],
        model_preference="heavy",
        confidence=0.85,
        description="Security-sensitive queries need careful, accurate responses",
    ),
]

# Pre-compile keyword patterns for each cluster
_COMPILED_CLUSTERS = []
for cluster in INTENT_CLUSTERS:
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in cluster.keywords]
    _COMPILED_CLUSTERS.append((cluster, patterns))


@dataclass
class SemanticResult:
    """Result of semantic pre-routing."""
    cluster_name: str = ""
    model_preference: str = ""  # "heavy", "free", "visual", "specialist"
    confidence: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)


def semantic_preroute(messages: list[dict]) -> SemanticResult | None:
    """
    Layer 2: Semantic pre-routing.

    Scans the last user message for keyword clusters.
    Returns a SemanticResult if a strong match is found, or None.
    """
    # Extract last user message text
    text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            break

    if not text:
        return None

    text_lower = text.lower()
    best_result = None
    best_match_count = 0

    for cluster, patterns in _COMPILED_CLUSTERS:
        matched = []
        for i, pattern in enumerate(patterns):
            if pattern.search(text):
                matched.append(cluster.keywords[i])

        if not matched:
            continue

        # Score: more keyword matches → higher confidence
        match_count = len(matched)
        # Boost confidence slightly for multiple matches
        boosted = min(cluster.confidence + (match_count - 1) * 0.03, 0.95)

        if match_count > best_match_count or (
            match_count == best_match_count and
            best_result and boosted > best_result.confidence
        ):
            best_result = SemanticResult(
                cluster_name=cluster.name,
                model_preference=cluster.model_preference,
                confidence=boosted,
                matched_keywords=matched,
            )
            best_match_count = match_count

    return best_result
