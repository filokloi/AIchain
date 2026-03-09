#!/usr/bin/env python3
"""Tests for catalog credential and helper runtime verifiers."""

from pathlib import Path

from tools.catalog_pipeline.credentials import load_openclaw_env_vars, resolve_credential
from tools.verify_helper_runtime import HelperRuntimeStatus, probe_helper_runtime
from tools.verify_helper_runtime import ForcedFailureProvider


def test_load_openclaw_env_vars_reads_literal_values(tmp_path: Path):
    cfg = tmp_path / 'openclaw.json'
    cfg.write_text('{"env":{"vars":{"GROQ_API_KEY":"gsk_test_123","IGNORED":"${SECRET}"}}}', encoding='utf-8')
    data = load_openclaw_env_vars(cfg)
    assert data['GROQ_API_KEY'] == 'gsk_test_123'
    assert 'IGNORED' not in data


def test_resolve_credential_prefers_env(monkeypatch, tmp_path: Path):
    cfg = tmp_path / 'openclaw.json'
    cfg.write_text('{"env":{"vars":{"GROQ_API_KEY":"gsk_config_123"}}}', encoding='utf-8')
    monkeypatch.setenv('GROQ_API_KEY', 'gsk_env_123')
    assert resolve_credential('GROQ_API_KEY', config_path=cfg) == 'gsk_env_123'


def test_forced_failure_provider_is_available():
    provider = ForcedFailureProvider()
    assert provider.available is True


def test_helper_runtime_status_dataclass_shape():
    status = HelperRuntimeStatus(
        status='runtime_confirmed',
        gemini_configured=True,
        groq_configured=True,
        gemini_runtime_confirmed=False,
        groq_runtime_confirmed=True,
        fallback_confirmed=True,
        reasons=[],
    )
    assert status.fallback_confirmed is True
