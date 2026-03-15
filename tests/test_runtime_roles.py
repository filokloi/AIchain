#!/usr/bin/env python3
"""Tests for runtime-safe role selection in aichaind.main."""

import logging

from aichaind.main import _sanitize_roles_for_runtime


class _Decision:
    def __init__(self, selected_method='disabled', status='disabled', runtime_confirmed=False):
        self.selected_method = selected_method
        self.status = status
        self.runtime_confirmed = runtime_confirmed


class _Layer:
    def resolve(self, provider):
        if provider == 'qwen':
            return _Decision(selected_method='disabled', status='disabled', runtime_confirmed=False)
        if provider == 'openrouter':
            return _Decision(selected_method='api_key', status='runtime_confirmed', runtime_confirmed=True)
        if provider == 'openai':
            return _Decision(selected_method='api_key', status='runtime_confirmed', runtime_confirmed=True)
        return _Decision(selected_method='disabled', status='disabled', runtime_confirmed=False)


def test_sanitize_roles_replaces_non_runtime_safe_catalog_heavy_role():
    roles = {
        'fast_brain': 'minimax/minimax-01',
        'heavy_brain': 'qwen/qwen3-235b-a22b-thinking-2507',
        'visual_brain': 'openai/gpt-4o:extended',
    }

    sanitized = _sanitize_roles_for_runtime(roles, _Layer(), logging.getLogger('test'))

    assert sanitized['heavy_brain'] == 'openrouter/google/gemini-2.5-pro'
    assert sanitized['fast_brain'] == 'minimax/minimax-01'
    assert sanitized['visual_brain'] == 'openai/gpt-4o:extended'
