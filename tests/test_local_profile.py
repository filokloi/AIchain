#!/usr/bin/env python3
"""Tests for local runtime profiling and store integration."""

from pathlib import Path

from aichaind.providers.local_profile import CapacityEstimate, LocalModelProfile, LocalProfileStore, profile_local_model
from aichaind.routing.cost_optimizer import CostOptimizer
from aichaind.providers.balance import BalanceReport


def test_local_profile_store_roundtrip(tmp_path):
    store = LocalProfileStore(tmp_path / 'local_profiles.json')
    profile = LocalModelProfile(
        provider='lmstudio',
        model='lmstudio/qwen/qwen3-4b-thinking-2507',
        base_url='http://127.0.0.1:1234/v1',
        profiled_at='2026-03-11T12:00:00Z',
        runtime_confirmed=True,
        success_rate=1.0,
        average_latency_ms=1200.0,
        average_ttft_ms=450.0,
        average_tokens_per_second=18.5,
        speed_score=88.0,
        stability_score=100.0,
        safe_timeout_ms=30000,
        prompt_type_suitability={'general_chat': 100.0},
    )
    store.upsert(profile)

    snapshot = store.snapshot()
    assert snapshot['profiles']['lmstudio/qwen/qwen3-4b-thinking-2507']['speed_score'] == 88.0
    summary = store.summary('lmstudio/qwen/qwen3-4b-thinking-2507')
    assert summary['total_profiles'] == 1
    assert summary['active_profile']['runtime_confirmed'] is True


def test_profile_local_model_aggregates_probe_metrics(monkeypatch):
    import aichaind.providers.local_profile as local_profile

    ttft_values = {
        'Reply with exactly LOCAL_PROFILE_OK.': 400.0,
        'What is 17 + 28? Reply with digits only.': 650.0,
        'Return only Python code for a function add(a, b) that returns a + b.': 900.0,
        'Return only minified JSON: {"ok":true,"answer":7}': 700.0,
    }
    responses = {
        'Reply with exactly LOCAL_PROFILE_OK.': (True, 'LOCAL_PROFILE_OK', 1200.0, 8, 'ok'),
        'What is 17 + 28? Reply with digits only.': (True, '45', 1800.0, 6, 'ok'),
        'Return only Python code for a function add(a, b) that returns a + b.': (True, 'def add(a, b):\n    return a + b', 2600.0, 22, 'ok'),
        'Return only minified JSON: {"ok":true,"answer":7}': (True, '{"ok":true,"answer":7}', 2000.0, 10, 'ok'),
    }

    monkeypatch.setattr(local_profile, '_stream_ttft', lambda base_url, payload, timeout: (ttft_values[payload['messages'][0]['content']], 'probe'))
    monkeypatch.setattr(local_profile, '_run_probe', lambda base_url, payload, timeout: responses[payload['messages'][0]['content']])
    monkeypatch.setattr(local_profile, 'estimate_lmstudio_capacity', lambda model: CapacityEstimate('capacity_ok', 'ok', 4.2))

    profile = profile_local_model('lmstudio', 'lmstudio/qwen/qwen3-4b-thinking-2507', 'http://127.0.0.1:1234/v1')
    assert profile.runtime_confirmed is True
    assert profile.success_rate == 1.0
    assert profile.average_ttft_ms == 662.5
    assert profile.capacity_status == 'capacity_ok'
    assert profile.prompt_type_suitability['coding'] == 100.0
    assert profile.safe_timeout_ms >= 20000


def test_cost_optimizer_uses_local_profile_signal_for_local_candidates():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    score_without_profile = optimizer._local_effective_score(
        pricing=optimizer._pricing_for_candidate(local_model, 'lmstudio'),
        model_preference='local',
        model_id=local_model,
        provider='lmstudio',
        access=optimizer._resolve_access('lmstudio'),
        estimated_cost_usd=0.0,
        available_models={'local': local_model},
    )
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 1.0,
                'speed_score': 90.0,
                'stability_score': 100.0,
                'capacity_status': 'capacity_ok',
                'prompt_type_suitability': {
                    'general_chat': 88.0,
                    'reasoning': 92.0,
                },
            }
        }
    })
    score_with_profile = optimizer._local_effective_score(
        pricing=optimizer._pricing_for_candidate(local_model, 'lmstudio'),
        model_preference='local',
        model_id=local_model,
        provider='lmstudio',
        access=optimizer._resolve_access('lmstudio'),
        estimated_cost_usd=0.0,
        available_models={'local': local_model},
    )
    assert score_with_profile > score_without_profile


def test_local_profile_snapshot_can_feed_optimizer_route_metadata():
    optimizer = CostOptimizer({'routing_hierarchy': []})
    local_model = 'lmstudio/qwen/qwen3-4b-thinking-2507'
    optimizer.configure_local_profiles({
        'profiles': {
            local_model: {
                'runtime_confirmed': True,
                'success_rate': 0.75,
                'speed_score': 72.0,
                'stability_score': 75.0,
                'capacity_status': 'capacity_ok',
                'prompt_type_suitability': {'general_chat': 80.0},
            }
        }
    })
    result = optimizer.optimize(
        model_preference='local',
        balance_report=BalanceReport(),
        available_models={'local': local_model, 'free': 'openrouter/google/gemini-2.5-flash:free'},
        exclude_models={'openrouter/google/gemini-2.5-flash:free'},
    )
    assert result.provider == 'lmstudio'
    assert result.model == local_model
    assert result.local_effective_score > 0


def test_profile_runtime_success_overrides_lmstudio_capacity_warning(monkeypatch):
    import aichaind.providers.local_profile as local_profile

    monkeypatch.setattr(local_profile, '_stream_ttft', lambda base_url, payload, timeout: (500.0, 'probe'))
    monkeypatch.setattr(local_profile, '_run_probe', lambda base_url, payload, timeout: (True, 'LOCAL_PROFILE_OK', 1200.0, 8, 'ok'))
    monkeypatch.setattr(local_profile, 'estimate_lmstudio_capacity', lambda model: CapacityEstimate('machine_capacity_blocked', 'estimate warning', 2.5))

    profile = profile_local_model('lmstudio', 'lmstudio/qwen/qwen3-4b-thinking-2507', 'http://127.0.0.1:1234/v1')
    assert profile.runtime_confirmed is True
    assert profile.capacity_status == 'capacity_estimate_conflict'
    assert 'Runtime probes succeeded despite estimate warning' in profile.capacity_detail
