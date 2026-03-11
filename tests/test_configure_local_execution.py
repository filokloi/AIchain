#!/usr/bin/env python3
"""Tests for tools.configure_local_execution."""

import json
from pathlib import Path

from aichaind.providers.local_runtime import LocalExecutionResolution, LocalRuntimeProbe
import tools.configure_local_execution as configure_local_execution



def test_configure_local_execution_writes_override_on_verified_completion(monkeypatch, tmp_path: Path):
    override_path = tmp_path / 'config.local.json'
    probe = LocalRuntimeProbe(
        provider='lmstudio',
        base_url='http://127.0.0.1:1234/v1',
        reachable=True,
        discovered_models=['lmstudio/qwen/qwen3.5-9b'],
        health_checked=True,
    )
    monkeypatch.setenv('AICHAIND_CONFIG_OVERRIDE', str(override_path))
    monkeypatch.setattr(
        configure_local_execution,
        'load_config',
        lambda path: {
            'local_execution': {
                'enabled': False,
                'provider': 'local',
                'default_model': '',
                'preferred_providers': ['lmstudio', 'local'],
                'require_healthcheck': True,
                'auto_detect': True,
            },
            '_config_sources': [str(path)],
        },
    )
    monkeypatch.setattr(
        configure_local_execution,
        'resolve_local_execution',
        lambda cfg, timeout=2.5, detect_when_disabled=True: LocalExecutionResolution(
            status='disabled',
            enabled=False,
            provider='',
            model='',
            base_url='',
            reason='local_execution.disabled',
            probes=[probe],
        ),
    )
    monkeypatch.setattr(configure_local_execution, 'select_best_local_runtime', lambda probes, preferred_providers=None, requested_model='': probe)
    monkeypatch.setattr(configure_local_execution, 'probe_local_completion', lambda provider, model, base_url='', timeout=90.0: (True, 'OK'))

    rc = configure_local_execution.main()

    assert rc == 0
    payload = json.loads(override_path.read_text(encoding='utf-8'))
    assert payload['local_execution']['enabled'] is True
    assert payload['local_execution']['provider'] == 'lmstudio'
    assert payload['local_execution']['default_model'] == 'lmstudio/qwen/qwen3.5-9b'



def test_configure_local_execution_refuses_override_when_completion_probe_fails(monkeypatch, tmp_path: Path):
    override_path = tmp_path / 'config.local.json'
    probe = LocalRuntimeProbe(
        provider='lmstudio',
        base_url='http://127.0.0.1:1234/v1',
        reachable=True,
        discovered_models=['lmstudio/qwen/qwen3.5-9b'],
        health_checked=True,
    )
    monkeypatch.setenv('AICHAIND_CONFIG_OVERRIDE', str(override_path))
    monkeypatch.setattr(
        configure_local_execution,
        'load_config',
        lambda path: {
            'local_execution': {
                'enabled': False,
                'provider': 'local',
                'default_model': '',
                'preferred_providers': ['lmstudio', 'local'],
                'require_healthcheck': True,
                'auto_detect': True,
            },
            '_config_sources': [str(path)],
        },
    )
    monkeypatch.setattr(
        configure_local_execution,
        'resolve_local_execution',
        lambda cfg, timeout=2.5, detect_when_disabled=True: LocalExecutionResolution(
            status='disabled',
            enabled=False,
            provider='',
            model='',
            base_url='',
            reason='local_execution.disabled',
            probes=[probe],
        ),
    )
    monkeypatch.setattr(configure_local_execution, 'select_best_local_runtime', lambda probes, preferred_providers=None, requested_model='': probe)
    monkeypatch.setattr(configure_local_execution, 'probe_local_completion', lambda provider, model, base_url='', timeout=90.0: (False, 'timeout'))

    rc = configure_local_execution.main()

    assert rc == 1
    assert override_path.exists() is False


def test_configure_local_execution_tries_multiple_candidates_until_one_succeeds(monkeypatch, tmp_path: Path):
    override_path = tmp_path / 'config.local.json'
    probe = LocalRuntimeProbe(
        provider='lmstudio',
        base_url='http://127.0.0.1:1234/v1',
        reachable=True,
        discovered_models=[
            'lmstudio/google/gemma-3-4b',
            'lmstudio/qwen/qwen3-4b-thinking-2507',
        ],
        health_checked=True,
    )
    monkeypatch.setenv('AICHAIND_CONFIG_OVERRIDE', str(override_path))
    monkeypatch.setattr(
        configure_local_execution,
        'load_config',
        lambda path: {
            'local_execution': {
                'enabled': True,
                'provider': 'lmstudio',
                'base_url': 'http://127.0.0.1:1234/v1',
                'default_model': 'lmstudio/qwen3-4b-local',
                'preferred_providers': ['lmstudio', 'local'],
                'require_healthcheck': True,
                'auto_detect': True,
            },
            '_config_sources': [str(path)],
        },
    )
    monkeypatch.setattr(
        configure_local_execution,
        'resolve_local_execution',
        lambda cfg, timeout=2.5, detect_when_disabled=True: LocalExecutionResolution(
            status='runtime_confirmed',
            enabled=True,
            provider='lmstudio',
            model='lmstudio/qwen3-4b-local',
            base_url='http://127.0.0.1:1234/v1',
            reason='local runtime reachable',
            probes=[probe],
        ),
    )
    monkeypatch.setattr(configure_local_execution, 'select_best_local_runtime', lambda probes, preferred_providers=None, requested_model='': probe)

    attempts = []

    def fake_probe(provider, model, base_url='', timeout=90.0):
        attempts.append(model)
        if model == 'lmstudio/google/gemma-3-4b':
            return False, 'insufficient resources'
        if model == 'lmstudio/qwen/qwen3-4b-thinking-2507':
            return True, 'OK'
        return False, 'model not found'

    monkeypatch.setattr(configure_local_execution, 'probe_local_completion', fake_probe)

    rc = configure_local_execution.main()

    assert rc == 0
    payload = json.loads(override_path.read_text(encoding='utf-8'))
    assert payload['local_execution']['default_model'] == 'lmstudio/qwen/qwen3-4b-thinking-2507'
    assert attempts == [
        'lmstudio/qwen3-4b-local',
        'lmstudio/google/gemma-3-4b',
        'lmstudio/qwen/qwen3-4b-thinking-2507',
    ]
