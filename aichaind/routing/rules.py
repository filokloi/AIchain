#!/usr/bin/env python3
"""
aichaind.routing.rules — Layer 1: Deterministic Rules & Heuristics

Extracted from aichain_bridge.py specialist pin logic.
Zero-cost, zero-latency routing decisions based on:
- Hard rules (godmode, specialist pins)
- Privacy gates (PII detected → local-only)
- Budget gates (over-budget → free-tier only)
- Pattern heuristics (image detection, code patterns, length)
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("aichaind.routing.rules")


# ─────────────────────────────────────────
# ROUTE DECISION
# ─────────────────────────────────────────

@dataclass
class RouteDecision:
    """Result of a routing decision."""
    target_model: str = ""
    target_provider: str = ""
    confidence: float = 0.0
    decision_layers: list[str] = field(default_factory=list)
    policy_checks: dict = field(default_factory=dict)
    fallback_chain: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    reason: str = ""


# ─────────────────────────────────────────
# SPECIALIST PIN MATCHING
# ─────────────────────────────────────────

# Default specialist pin definitions (from bridge_config.json)
DEFAULT_SPECIALIST_PINS = {
    "vision": {
        "triggers": [
            "image_analysis", "facial_recognition", "ocr",
            "screenshot", "visual_analysis", "photo",
            "face_detect", "image_forensics", "slika", "slike"
        ],
        "model": "google/gemini-2.5-pro",
        "ttl_minutes": 30,
    },
    "deep_research": {
        "triggers": [
            "deep_web_search", "evidence_synthesis", "data_correlation",
            "intelligence_report", "target_analysis"
        ],
        "model": "openai/o3-pro",
        "ttl_minutes": 30,
    },
    "code_engineering": {
        "triggers": [
            "refactor", "system_architecture", "reverse_engineer",
            "exploit_analysis"
        ],
        "model": "openai/gpt-4.1",
        "ttl_minutes": 30,
    },
    "document_analysis": {
        "triggers": [
            "pdf_analysis", "document_forensics", "extract_text",
            "contract_review", "legal_analysis"
        ],
        "model": "anthropic/claude-sonnet-4",
        "ttl_minutes": 30,
    },
}


def check_specialist_pin(text: str, specialist_pins: dict = None) -> Optional[dict]:
    """
    Check if text matches any specialist pin trigger.
    Returns pin config dict or None.
    """
    pins = specialist_pins or DEFAULT_SPECIALIST_PINS
    text_lower = text.lower()
    for category, pin in pins.items():
        for trigger in pin.get("triggers", []):
            if trigger.lower() in text_lower:
                log.info(f"Specialist pin triggered: {category} → {pin['model']}")
                return {"category": category, **pin}
    return None


# ─────────────────────────────────────────
# VISUAL DETECTION
# ─────────────────────────────────────────

def detect_visual_content(messages: list[dict]) -> bool:
    """Detect if messages contain image/visual content (0ms heuristic)."""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


# ─────────────────────────────────────────
# COMPLEXITY HEURISTICS
# ─────────────────────────────────────────

# Patterns that suggest complex queries
CODE_PATTERNS = re.compile(
    r"(```|def |class |import |function |const |var |let |SELECT |CREATE TABLE|async |await )",
    re.IGNORECASE
)
MATH_PATTERNS = re.compile(
    r"(\d+\s*[+\-*/^]\s*\d+|integral|derivative|equation|theorem|proof|optimize|minimize|maximize)",
    re.IGNORECASE
)
RESEARCH_PATTERNS = re.compile(
    r"(analyze|compare|evaluate|synthesize|review|assess|investigate|explain in depth)",
    re.IGNORECASE
)


def estimate_complexity(text: str) -> tuple[str, float]:
    """
    Estimate query complexity via heuristics.
    Returns (category, confidence).
    category: "quick" or "analyst"
    confidence: 0.0–1.0
    """
    if not text:
        return "quick", 0.9

    word_count = len(text.split())

    # Very short → quick with high confidence
    if word_count < 10:
        return "quick", 0.85

    # Very long → probably complex
    if word_count > 200:
        return "analyst", 0.75

    # Code presence → analyst
    if CODE_PATTERNS.search(text):
        return "analyst", 0.80

    # Math/science → analyst
    if MATH_PATTERNS.search(text):
        return "analyst", 0.75

    # Research language → analyst
    if RESEARCH_PATTERNS.search(text):
        return "analyst", 0.70

    # Medium length, no strong signals → low confidence
    if word_count > 50:
        return "analyst", 0.55

    return "quick", 0.60


# ─────────────────────────────────────────
# LAYER 1 ROUTER
# ─────────────────────────────────────────

def layer1_route(
    messages: list[dict],
    godmode_model: Optional[str] = None,
    specialist_pins: dict = None,
    budget_state=None,
    privacy_context=None,
    available_free_model: str = "",
    available_visual_model: str = "",
) -> Optional[RouteDecision]:
    """
    Layer 1: Deterministic rules and heuristics.
    Returns a RouteDecision if confident enough, None to pass to Layer 2.
    """
    policy_checks = {
        "budget_ok": True,
        "privacy_ok": True,
    }

    # 1. Godmode — absolute override
    if godmode_model:
        return RouteDecision(
            target_model=godmode_model,
            confidence=1.0,
            decision_layers=["L1:godmode"],
            policy_checks=policy_checks,
            reason="godmode_active",
        )

    # 2. Budget gate
    if budget_state and budget_state.over_budget:
        policy_checks["budget_ok"] = False
        if available_free_model:
            return RouteDecision(
                target_model=available_free_model,
                confidence=0.95,
                decision_layers=["L1:budget_gate"],
                policy_checks=policy_checks,
                reason="over_budget_forced_free",
            )

    # 3. Privacy gate
    if privacy_context and not privacy_context.cloud_routing_allowed:
        policy_checks["privacy_ok"] = False
        # Would need a local-only model list — for now, flag it
        log.warning("Privacy gate: cloud routing blocked, need local model")

    # Extract last user message
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                last_user_msg = content
            elif isinstance(content, list):
                last_user_msg = " ".join(
                    [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                )
            break

    # 4. Visual detection
    if detect_visual_content(messages):
        if available_visual_model:
            return RouteDecision(
                target_model=available_visual_model,
                confidence=0.95,
                decision_layers=["L1:visual_detect"],
                policy_checks=policy_checks,
                reason="visual_content_detected",
            )

    # 5. Specialist pin
    pin = check_specialist_pin(last_user_msg, specialist_pins)
    if pin:
        return RouteDecision(
            target_model=pin["model"],
            confidence=0.90,
            decision_layers=["L1:specialist_pin"],
            policy_checks=policy_checks,
            reason=f"specialist_{pin['category']}",
        )

    # 6. Complexity heuristics
    category, confidence = estimate_complexity(last_user_msg)
    if confidence >= 0.85:
        return RouteDecision(
            target_model="",  # To be filled by caller based on category
            confidence=confidence,
            decision_layers=["L1:heuristic"],
            policy_checks=policy_checks,
            reason=f"heuristic_{category}",
        )

    # Below threshold — pass to Layer 2
    return None
