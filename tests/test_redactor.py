#!/usr/bin/env python3
"""
Tests for aichaind.security.redactor — PII Redaction Pipeline

Covers:
- Email detection & redaction
- Phone number detection
- SSN, credit card, IP address
- JMBG (Serbian 13-digit ID)
- Reversible redaction map
- De-redaction
- Multi-modal message redaction
- Scan-only mode
"""

import pytest
from aichaind.security.redactor import PIIRedactor, redact_messages, scan_messages


class TestPIIRedactor:
    def test_email_detected(self):
        r = PIIRedactor()
        result = r.redact("Contact me at john@example.com please")
        assert result.pii_found is True
        assert "email" in result.pii_categories
        assert "john@example.com" not in result.redacted_text
        assert "[EMAIL_" in result.redacted_text

    def test_multiple_emails(self):
        r = PIIRedactor()
        result = r.redact("Send to alice@test.com and bob@test.com")
        assert result.total_redactions >= 2
        assert "alice@test.com" not in result.redacted_text
        assert "bob@test.com" not in result.redacted_text

    def test_phone_us(self):
        r = PIIRedactor()
        result = r.redact("Call me at (555) 123-4567")
        assert result.pii_found is True
        assert "phone" in " ".join(result.pii_categories)

    def test_phone_intl(self):
        r = PIIRedactor()
        result = r.redact("Reach me at +381 64 1234567")
        assert result.pii_found is True

    def test_ssn(self):
        r = PIIRedactor()
        result = r.redact("My SSN is 123-45-6789")
        assert result.pii_found is True
        assert "123-45-6789" not in result.redacted_text

    def test_credit_card(self):
        r = PIIRedactor()
        result = r.redact("Card: 4111-1111-1111-1111")
        assert result.pii_found is True
        assert "4111" not in result.redacted_text

    def test_ip_address(self):
        r = PIIRedactor()
        result = r.redact("Server at 192.168.1.100")
        assert result.pii_found is True
        assert "192.168.1.100" not in result.redacted_text

    def test_jmbg_serbian(self):
        r = PIIRedactor()
        result = r.redact("JMBG: 0101990710123")
        assert result.pii_found is True
        assert "0101990710123" not in result.redacted_text

    def test_no_pii(self):
        r = PIIRedactor()
        result = r.redact("What is the weather in Belgrade?")
        assert result.pii_found is False
        assert result.total_redactions == 0
        assert result.redacted_text == "What is the weather in Belgrade?"

    def test_reversible_redaction(self):
        r = PIIRedactor()
        original = "Email john@test.com for info"
        result = r.redact(original)
        restored = r.de_redact(result.redacted_text, result.redaction_map)
        assert restored == original

    def test_consistent_placeholders_across_turns(self):
        r = PIIRedactor()
        r1 = r.redact("Contact john@test.com")
        # Second turn with same map
        r2 = r.redact("Also email john@test.com for details", r1.redaction_map)
        # Should use same placeholder
        assert r1.redaction_map == r2.redaction_map or \
               any(v == "john@test.com" for v in r2.redaction_map.values())

    def test_scan_only_doesnt_modify(self):
        r = PIIRedactor()
        text = "Email: test@example.com"
        result = r.scan_only(text)
        assert result.pii_found is True
        assert result.redacted_text == text  # unchanged

    def test_enabled_categories_filter(self):
        r = PIIRedactor(enabled_categories=["email"])
        result = r.redact("Email test@x.com, SSN 123-45-6789")
        assert "email" in result.pii_categories
        assert "ssn" not in result.pii_categories


class TestRedactMessages:
    def test_redact_simple_messages(self):
        msgs = [
            {"role": "user", "content": "My email is user@test.com"},
            {"role": "assistant", "content": "Got it"},
        ]
        redacted, rmap, cats = redact_messages(msgs)
        assert "user@test.com" not in redacted[0]["content"]
        assert "email" in cats

    def test_redact_multimodal_messages(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "Call me at 555-123-4567"},
            {"type": "image_url", "image_url": {"url": "http://img.png"}},
        ]}]
        redacted, rmap, cats = redact_messages(msgs)
        text_part = redacted[0]["content"][0]["text"]
        assert "555-123-4567" not in text_part
        # Image part preserved
        assert redacted[0]["content"][1]["type"] == "image_url"

    def test_no_pii_passes_through(self):
        msgs = [{"role": "user", "content": "Hello world"}]
        redacted, rmap, cats = redact_messages(msgs)
        assert redacted[0]["content"] == "Hello world"
        assert len(rmap) == 0

    def test_scan_messages_detects_without_modifying(self):
        msgs = [{"role": "user", "content": "My SSN is 123-45-6789 and email is x@test.com"}]
        cats = scan_messages(msgs)
        assert "ssn" in cats
        assert "email" in cats

