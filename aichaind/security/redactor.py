#!/usr/bin/env python3
"""
aichaind.security.redactor — PII Redaction Pipeline

Regex-based PII detection and redaction before cloud routing.
Maintains a reversible redaction map in the session.
"""

import re
import logging
from dataclasses import dataclass, field

log = logging.getLogger("aichaind.security.redactor")


# ─────────────────────────────────────────
# PII PATTERNS
# ─────────────────────────────────────────

PII_PATTERNS = {
    "email": re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"
    ),
    "phone_intl": re.compile(
        r"\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{0,4}"
    ),
    "phone_us": re.compile(
        r"\b\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"
    ),
    "ssn": re.compile(
        r"\b\d{3}[\-\s]?\d{2}[\-\s]?\d{4}\b"
    ),
    "credit_card": re.compile(
        r"\b(?:\d{4}[\s\-]?){3}\d{4}\b"
    ),
    "ip_address": re.compile(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    ),
    "jmbg": re.compile(  # Serbian JMBG (13 digits)
        r"\b\d{13}\b"
    ),
    "date_of_birth": re.compile(
        r"\b(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\b"
    ),
    "passport": re.compile(
        r"\b[A-Z]{1,2}\d{6,9}\b"
    ),
}


@dataclass
class RedactionResult:
    """Result of PII redaction."""
    redacted_text: str = ""
    redaction_map: dict = field(default_factory=dict)  # placeholder → original
    pii_found: bool = False
    pii_categories: list[str] = field(default_factory=list)
    total_redactions: int = 0


class PIIRedactor:
    """
    Regex-based PII redactor.

    Scans text for PII patterns, replaces with placeholders,
    and maintains a reversible map for de-redaction if needed.
    """

    def __init__(self, patterns: dict = None, enabled_categories: list[str] = None):
        self.patterns = patterns or PII_PATTERNS
        if enabled_categories:
            self.patterns = {k: v for k, v in self.patterns.items()
                           if k in enabled_categories}

    def redact(self, text: str, existing_map: dict = None) -> RedactionResult:
        """
        Scan and redact PII from text.

        Args:
            text: Input text to scan
            existing_map: Previous redaction map to extend (for consistency across turns)

        Returns:
            RedactionResult with redacted text and reversible map
        """
        result = RedactionResult(redacted_text=text)
        redaction_map = dict(existing_map) if existing_map else {}

        # Build reverse lookup for re-using existing placeholders
        reverse_map = {v: k for k, v in redaction_map.items()}

        counter_by_cat = {}

        for category, pattern in self.patterns.items():
            matches = pattern.findall(text)
            if not matches:
                continue

            result.pii_found = True
            if category not in result.pii_categories:
                result.pii_categories.append(category)

            for match in set(matches):  # dedupe
                if match in reverse_map:
                    # Reuse existing placeholder for consistency
                    placeholder = reverse_map[match]
                else:
                    # Generate new placeholder
                    cat_upper = category.upper()
                    idx = counter_by_cat.get(category, len(
                        [k for k in redaction_map if cat_upper in k]
                    )) + 1
                    counter_by_cat[category] = idx
                    placeholder = f"[{cat_upper}_{idx}]"
                    redaction_map[placeholder] = match
                    reverse_map[match] = placeholder

                result.redacted_text = result.redacted_text.replace(match, placeholder)
                result.total_redactions += 1

        result.redaction_map = redaction_map
        return result

    def de_redact(self, text: str, redaction_map: dict) -> str:
        """Reverse redaction using the redaction map."""
        result = text
        for placeholder, original in redaction_map.items():
            result = result.replace(placeholder, original)
        return result

    def scan_only(self, text: str) -> RedactionResult:
        """Scan for PII without modifying text. For detection/audit only."""
        result = RedactionResult(redacted_text=text)
        for category, pattern in self.patterns.items():
            if pattern.search(text):
                result.pii_found = True
                result.pii_categories.append(category)
        return result


def redact_messages(messages: list[dict], redactor: PIIRedactor = None,
                    existing_map: dict = None) -> tuple[list[dict], dict, list[str]]:
    """
    Redact PII from a list of chat messages.

    Returns:
        (redacted_messages, merged_redaction_map, pii_categories)
    """
    if redactor is None:
        redactor = PIIRedactor()

    merged_map = dict(existing_map) if existing_map else {}
    all_categories = []
    redacted_msgs = []

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            result = redactor.redact(content, merged_map)
            merged_map.update(result.redaction_map)
            all_categories.extend(result.pii_categories)
            redacted_msgs.append({**msg, "content": result.redacted_text})
        elif isinstance(content, list):
            # Multi-modal: redact text parts only
            new_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    result = redactor.redact(part.get("text", ""), merged_map)
                    merged_map.update(result.redaction_map)
                    all_categories.extend(result.pii_categories)
                    new_parts.append({**part, "text": result.redacted_text})
                else:
                    new_parts.append(part)
            redacted_msgs.append({**msg, "content": new_parts})
        else:
            redacted_msgs.append(msg)

    return redacted_msgs, merged_map, list(set(all_categories))
