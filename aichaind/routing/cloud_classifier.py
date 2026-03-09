#!/usr/bin/env python3
"""
aichaind.routing.cloud_classifier — Layer 4: Cloud-based Classifier

Uses a fast/free AI model (Brain A) to classify routing intent.
This is the highest-accuracy but highest-latency layer (~200-2000ms).
Only invoked when Layers 1-3 fail to reach confidence threshold.

Design:
  - Uses the Fast Brain model (free) to do a quick classification
  - Structured prompt → JSON response → parse into RouteDecision
  - Feature-flagged, degrades gracefully on timeout
"""

import json
import time
import logging
from dataclasses import dataclass

log = logging.getLogger("aichaind.routing.cloud_classifier")

CLASSIFIER_PROMPT = """You are a routing classifier. Given a user query, classify it into EXACTLY ONE category.

Categories:
- HEAVY_REASONING: complex math, proofs, algorithms, deep analysis
- HEAVY_CODE: code generation, debugging, refactoring, architecture
- HEAVY_CREATIVE: creative writing, stories, essays, poetry
- FREE_SIMPLE: simple questions, greetings, translations, formatting
- VISUAL: image analysis, screenshots, UI design

Respond with ONLY a JSON object: {"category": "CATEGORY_NAME", "confidence": 0.0-1.0}

User query: {query}"""


@dataclass
class CloudClassification:
    """Result from cloud classifier."""
    category: str = ""
    model_preference: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    model_used: str = ""
    raw_response: str = ""


_CATEGORY_TO_PREF = {
    "HEAVY_REASONING": "heavy",
    "HEAVY_CODE": "heavy",
    "HEAVY_CREATIVE": "heavy",
    "FREE_SIMPLE": "free",
    "VISUAL": "visual",
}


class CloudClassifier:
    """
    Layer 4: Cloud-based routing classifier.

    Uses a fast model to classify queries when local layers
    can't reach the confidence threshold.
    """

    def __init__(self, timeout_ms: float = 2000.0):
        self.timeout_ms = timeout_ms
        self._adapter = None
        self._model = ""

    def configure(self, adapter, model: str):
        """Set the adapter and model to use for classification."""
        self._adapter = adapter
        self._model = model

    def classify(self, messages: list[dict]) -> CloudClassification | None:
        """
        Classify a query using a cloud model.
        Returns None on timeout/error.
        """
        if not self._adapter or not self._model:
            log.debug("Cloud classifier not configured")
            return None

        # Extract last user message
        query = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    query = content[:500]  # Truncate for classification
                elif isinstance(content, list):
                    query = " ".join(
                        p.get("text", "")[:200] for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                break

        if not query:
            return None

        # Build classification request
        from aichaind.providers.base import CompletionRequest

        prompt = CLASSIFIER_PROMPT.format(query=query)
        request = CompletionRequest(
            model=self._adapter.format_model_id(self._model),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=64,
            temperature=0.0,
        )

        start_t = time.time()
        try:
            response = self._adapter.execute(request)
            latency = (time.time() - start_t) * 1000

            if latency > self.timeout_ms:
                log.warning(f"Cloud classifier too slow: {latency:.0f}ms")
                return None

            if response.status != "success" or not response.content:
                return None

            # Parse JSON response
            return self._parse_response(response.content, latency, self._model)

        except Exception as e:
            log.error(f"Cloud classifier error: {e}")
            return None

    def _parse_response(self, content: str, latency_ms: float,
                       model: str) -> CloudClassification | None:
        """Parse the classifier's JSON response."""
        try:
            # Try to extract JSON from response
            content = content.strip()
            if content.startswith("```"):
                # Strip code fences
                lines = content.split("\n")
                content = "\n".join(
                    l for l in lines if not l.strip().startswith("```")
                )

            data = json.loads(content)
            category = data.get("category", "").upper()
            confidence = float(data.get("confidence", 0.0))

            if category not in _CATEGORY_TO_PREF:
                log.warning(f"Unknown category from cloud: {category}")
                return None

            return CloudClassification(
                category=category,
                model_preference=_CATEGORY_TO_PREF[category],
                confidence=min(confidence, 0.98),  # Cap at 0.98
                latency_ms=latency_ms,
                model_used=model,
                raw_response=content,
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            log.warning(f"Cloud classifier parse error: {e}")
            return None
