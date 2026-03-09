#!/usr/bin/env python3
"""
aichaind.routing.encoder — Layer 3: Local TF-IDF Encoder Scorer

Lightweight local routing scorer using TF-IDF vectorization.
No external ML dependencies — uses pure Python math.
Classifies queries into routing categories with confidence scores.

Design:
  - Pre-built TF-IDF reference vectors for each routing category
  - Cosine similarity scoring against input text
  - 10-50ms latency, fully offline
"""

import math
import re
import logging
from dataclasses import dataclass, field
from collections import Counter

log = logging.getLogger("aichaind.routing.encoder")


# ─────────────────────────────────────────
# ROUTING CATEGORY REFERENCE DOCUMENTS
# ─────────────────────────────────────────

CATEGORY_DOCS = {
    "heavy_reasoning": (
        "analyze prove theorem mathematical proof derive induction contradiction "
        "deduction hypothesis axiom lemma corollary postulate algorithm complexity "
        "NP-hard dynamic programming recursive optimization formal verification "
        "logic predicate calculus boolean algebra set theory graph theory "
        "differential equations linear algebra eigenvalue matrix decomposition "
        "statistical inference bayesian probability regression correlation "
        "step by step chain of thought reasoning systematic analysis deep dive "
        "comprehensive evaluation root cause investigation research synthesis"
    ),
    "heavy_code": (
        "implement function class method refactor debug fix error exception "
        "traceback compile runtime syntax type interface abstract inheritance "
        "polymorphism encapsulation design pattern factory singleton observer "
        "REST API endpoint database schema migration SQL query ORM model "
        "unit test integration test mock stub fixture assertion coverage "
        "docker kubernetes deployment CI CD pipeline github actions workflow "
        "webpack vite build bundle minify transpile lint format "
        "async await promise callback event loop concurrency thread mutex "
        "architecture microservices serverless lambda edge computing "
        "security vulnerability injection XSS CSRF authentication authorization"
    ),
    "heavy_creative": (
        "write story novel chapter narrative character development dialogue "
        "screenplay script plot arc setting world building creative fiction "
        "poem poetry verse stanza rhyme meter sonnet haiku limerick "
        "essay article blog post editorial opinion persuasive argumentative "
        "copywriting marketing slogan tagline brand voice tone style "
        "translate localize adaptation cultural context nuance idiom "
        "metaphor simile allegory symbolism imagery personification "
        "genre fantasy science fiction mystery thriller romance horror "
        "critique review analysis literary interpretation thematic motif"
    ),
    "free_simple": (
        "what is who is when where how many define meaning explain "
        "translate convert calculate simple answer quick fact "
        "weather time date capital population distance height weight "
        "yes no true false correct wrong right left up down "
        "hello hi hey thanks thank you goodbye bye please help "
        "list name give me tell me show me find me look up search "
        "summarize brief short quick overview recap highlight key points "
        "format rewrite paraphrase bullet table markdown"
    ),
    "visual": (
        "image picture photo screenshot diagram chart graph visualization "
        "look at this see this view analyze image describe picture "
        "OCR text recognition read image extract text from image "
        "UI user interface design mockup wireframe layout component "
        "color palette theme aesthetic visual style branding logo icon "
        "data visualization dashboard infographic map heatmap scatter plot "
        "medical imaging x-ray scan MRI CT ultrasound pathology slide"
    ),
}


# ─────────────────────────────────────────
# TF-IDF ENGINE
# ─────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Term frequency (normalized)."""
    counts = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {t: c / total for t, c in counts.items()}


def _compute_idf(corpus: list[list[str]]) -> dict[str, float]:
    """Inverse document frequency."""
    n_docs = len(corpus)
    df = Counter()
    for doc_tokens in corpus:
        for t in set(doc_tokens):
            df[t] += 1
    return {t: math.log((n_docs + 1) / (f + 1)) + 1 for t, f in df.items()}


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """TF-IDF vector for a document."""
    tf = _compute_tf(tokens)
    return {t: tf_val * idf.get(t, 1.0) for t, tf_val in tf.items()}


def _cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors."""
    common = set(v1.keys()) & set(v2.keys())
    if not common:
        return 0.0

    dot = sum(v1[t] * v2[t] for t in common)
    norm1 = math.sqrt(sum(v ** 2 for v in v1.values()))
    norm2 = math.sqrt(sum(v ** 2 for v in v2.values()))

    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


@dataclass
class EncoderResult:
    """Result of Layer 3 encoding."""
    category: str = ""
    model_preference: str = ""  # "heavy", "free", "visual"
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)


# Category -> model preference mapping
_CATEGORY_PREF = {
    "heavy_reasoning": "heavy",
    "heavy_code": "heavy",
    "heavy_creative": "heavy",
    "free_simple": "free",
    "visual": "visual",
}


class LocalEncoderScorer:
    """
    TF-IDF based local scorer for routing classification.
    Pre-computes reference vectors on init (~1ms).
    Scoring a query takes ~1-5ms.
    """

    def __init__(self):
        # Tokenize all category docs
        self._cat_tokens = {
            cat: _tokenize(doc) for cat, doc in CATEGORY_DOCS.items()
        }

        # Build corpus IDF
        all_tokens = list(self._cat_tokens.values())
        self._idf = _compute_idf(all_tokens)

        # Pre-compute reference vectors
        self._ref_vectors = {
            cat: _tfidf_vector(tokens, self._idf)
            for cat, tokens in self._cat_tokens.items()
        }

    def score(self, messages: list[dict]) -> EncoderResult | None:
        """
        Score a message list against routing categories.
        Returns the best-matching category with confidence.
        """
        # Extract last user message
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

        if not text or len(text) < 5:
            return None

        # Tokenize query
        query_tokens = _tokenize(text)
        if not query_tokens:
            return None

        query_vec = _tfidf_vector(query_tokens, self._idf)

        # Score against all categories
        scores = {}
        for cat, ref_vec in self._ref_vectors.items():
            scores[cat] = _cosine_similarity(query_vec, ref_vec)

        # Find best
        best_cat = max(scores, key=scores.get)
        best_score = scores[best_cat]

        if best_score < 0.05:
            return None

        # Normalize confidence to 0.6-0.95 range
        confidence = min(0.6 + best_score * 2.0, 0.95)

        return EncoderResult(
            category=best_cat,
            model_preference=_CATEGORY_PREF.get(best_cat, "heavy"),
            confidence=confidence,
            scores=scores,
        )
