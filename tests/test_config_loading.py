#!/usr/bin/env python3
"""Tests for config loading with user-local overrides."""

import json
from pathlib import Path

from aichaind.core.state_machine import load_config


def test_load_config_merges_user_override(monkeypatch, tmp_path: Path):
    base = tmp_path / 'default.json'
    override = tmp_path / 'config.local.json'
    base.write_text(json.dumps({
        'routing_url': 'https://example.com/base.json',
        'local_execution': {
            'enabled': False,
            'provider': 'local',
            'base_url': 'http://127.0.0.1:11434/v1',
            'default_model': '',
        },
        'policy': {
            'pii_blocks_cloud': False,
            'pii_prefer_local': False,
            'max_cost_per_turn': 1.0,
        },
    }), encoding='utf-8')
    override.write_text(json.dumps({
        'local_execution': {
            'enabled': True,
            'provider': 'lmstudio',
            'base_url': 'http://127.0.0.1:1234/v1',
            'default_model': 'lmstudio/qwen/qwen3.5-9b',
        },
        'policy': {
            'max_cost_per_turn': 0.5,
        },
    }), encoding='utf-8')
    monkeypatch.setenv('AICHAIND_CONFIG_OVERRIDE', str(override))

    cfg = load_config(base)

    assert cfg['routing_url'] == 'https://example.com/base.json'
    assert cfg['local_execution']['enabled'] is True
    assert cfg['local_execution']['provider'] == 'lmstudio'
    assert cfg['policy']['pii_blocks_cloud'] is False
    assert cfg['policy']['pii_prefer_local'] is False
    assert cfg['policy']['max_cost_per_turn'] == 0.5
    assert cfg['_config_sources'] == [str(base), str(override)]


def test_load_config_uses_base_only_when_override_missing(monkeypatch, tmp_path: Path):
    base = tmp_path / 'default.json'
    base.write_text(json.dumps({'local_execution': {'enabled': False}}), encoding='utf-8')
    monkeypatch.setenv('AICHAIND_CONFIG_OVERRIDE', str(tmp_path / 'missing.json'))

    cfg = load_config(base)

    assert cfg['local_execution']['enabled'] is False
    assert cfg['_config_sources'] == [str(base)]
