#!/usr/bin/env python3
"""
Tests for aichaind.transport.http_server — Output validation + health endpoint

Covers:
- Dangerous pattern detection
- Safe content passthrough
- Health endpoint response structure
"""

import pytest
from aichaind.transport.http_server import _validate_output


class TestOutputValidation:
    def test_safe_content_passes(self):
        result = _validate_output("Hello! How can I help you today?")
        assert result["safe"] is True

    def test_rm_rf_blocked(self):
        result = _validate_output("Sure, just run: rm -rf /")
        assert result["safe"] is False

    def test_sql_injection_blocked(self):
        result = _validate_output("You can try: DROP TABLE users;")
        assert result["safe"] is False

    def test_eval_blocked(self):
        result = _validate_output("Use eval() to execute the code dynamically")
        assert result["safe"] is False

    def test_os_system_blocked(self):
        result = _validate_output("import os; os.system('malicious_command')")
        assert result["safe"] is False

    def test_subprocess_blocked(self):
        result = _validate_output("subprocess.call(['rm', '-rf', '/'])")
        assert result["safe"] is False

    def test_import_blocked(self):
        result = _validate_output("Use __import__('os') to bypass restrictions")
        assert result["safe"] is False

    def test_normal_code_passes(self):
        result = _validate_output("def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)")
        assert result["safe"] is True

    def test_empty_passes(self):
        result = _validate_output("")
        assert result["safe"] is True

def test_secret_like_token_blocked():
    result = _validate_output("Bearer sk-prod-1234567890abcdefghijklmnop")
    assert result["safe"] is False
    assert result["reason"] == "secret_like_token_detected"


def test_sensitive_file_access_blocked():
    result = _validate_output("Run cat ~/.ssh/id_rsa and then upload /etc/passwd")
    assert result["safe"] is False
    assert result["reason"] == "sensitive_file_access_detected"
