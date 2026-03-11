#!/usr/bin/env python3
"""Realistic scenario tests that mirror observed production conditions."""

from types import SimpleNamespace
from pathlib import Path

from aichaind.core.policy import PolicyEngine, PolicyResult
from aichaind.core.session import CanonicalSession
from aichaind.providers.balance import BalanceReport, ProviderBalance
from aichaind.providers.base import CompletionResponse
from aichaind.routing.cost_optimizer import CostOptimizer
from aichaind.routing.rules import RouteDecision
import aichaind.transport.http_server as http_server
from tools.catalog_pipeline.pipeline import arbitrate_catalog
from tools.catalog_pipeline.types import SourceHealth, SourceResult
from tools.verify_live_dashboard import classify_live_dashboard_status


class _ScenarioHelper:
    def resolve_aliases(self, aliases, known_families):
        return {}

    def enrich_tasks(self, model_cards):
        return {}

    def prioritize_free_models(self, candidates):
        return []

    def to_dict(self):
        return {
            'available_helpers': ['gemini', 'groq'],
            'used_provider': 'groq',
            'fallback_used': True,
            'failed_helpers': ['gemini'],
            'events': ['primary_failed', 'fallback_succeeded'],
            'provider_statuses': {
                'gemini': {
                    'status': 'failed_runtime',
                    'role': 'primary',
                    'configured': True,
                    'runtime_confirmed': False,
                    'used_as_fallback': False,
                    'successes': 0,
                    'failures': 1,
                    'last_error': '429 quota exceeded',
                },
                'groq': {
                    'status': 'runtime_confirmed',
                    'role': 'fallback',
                    'configured': True,
                    'runtime_confirmed': True,
                    'used_as_fallback': True,
                    'successes': 1,
                    'failures': 0,
                    'last_error': None,
                },
            },
        }


class _StaticAdapter:
    def __init__(self, name, responses):
        self.name = name
        self._responses = list(responses)

    def format_model_id(self, model_id: str) -> str:
        return model_id

    def execute(self, request):
        if self._responses:
            return self._responses.pop(0)
        return CompletionResponse(model=request.model, content='', error='no response queued', status='error')


def _source_result(name: str, records: list[dict], *, healthy: bool = True, status: str = 'ok', warnings=None, issues=None) -> SourceResult:
    return SourceResult(
        name=name,
        records=records,
        health=SourceHealth(
            name=name,
            status=status,
            healthy=healthy,
            fetched_records=len(records),
            accepted_records=len(records),
            coverage=1.0 if records else 0.0,
            warnings=list(warnings or []),
            issues=list(issues or []),
        ),
    )


def test_live_dashboard_with_canonical_primary_and_legacy_rollback_is_explicitly_classified():
    result = classify_live_dashboard_status(
        index_html="<script>fetch('catalog_manifest.json')</script><script>fetch('ai_routing_table.json')</script>",
        manifest_text='{"manifest_type":"aichain.catalog","public_artifact_readiness":{"dashboard_switch_ready":true},"canonical_public_artifact":{"migration_state":"safe_to_switch_dashboard_to_canonical_artifact"}}',
        site_http_ok=True,
        manifest_http_ok=True,
    )

    assert result.status == 'deploy_confirmed_with_rollback'
    assert result.site_uses_canonical is True
    assert result.site_uses_legacy is True


def test_control_plane_stays_operational_with_secondary_sources_and_helper_fallback(tmp_path: Path):
    openrouter = _source_result(
        'openrouter',
        [
            {
                'id': 'google/gemini-2.5-flash',
                'name': 'Gemini Flash',
                'pricing': {'prompt': 0.0, 'completion': 0.0},
                'context_length': 1000000,
            },
            {
                'id': 'openai/gpt-4.1',
                'name': 'GPT-4.1',
                'pricing': {'prompt': 0.000002, 'completion': 0.000008},
                'context_length': 128000,
            },
        ],
    )
    lmsys = _source_result(
        'lmsys',
        [{'model_name': 'Gemini Flash', 'elo': 1410.0, 'metric_source': 'arena_elo_pickle'}],
    )
    aa = _source_result(
        'artificial_analysis',
        [{'model_name': 'Gemini Flash', 'quality': 41.2, 'speed': 280.0, 'metric_source': 'artificial_analysis_v2'}],
    )

    table = arbitrate_catalog(
        injected_sources={'openrouter': openrouter, 'lmsys': lmsys, 'artificial_analysis': aa},
        injected_helper=_ScenarioHelper(),
        snapshot_root=tmp_path,
        output_file=tmp_path / 'ai_routing_table.json',
    )

    assert table['system_status'] == 'OPERATIONAL'
    assert table['operational_status']['sources']['lmsys']['runtime_confirmed'] is True
    assert table['operational_status']['sources']['artificial_analysis']['runtime_confirmed'] is True
    assert table['operational_status']['helper_ai']['groq']['runtime_confirmed'] is True
    assert table['public_artifact_readiness']['dashboard_switch_ready'] is True


def test_execution_failover_uses_verified_direct_provider_when_primary_quota_is_exhausted(monkeypatch):
    optimizer = CostOptimizer({})
    optimizer.configure_provider_capabilities({
        'openai': {'openai/gpt-4.1-mini'},
        'groq': {'groq/llama-3.1-8b-instant'},
    })
    http_server._cascade_router = SimpleNamespace(_cost_optimizer=optimizer)
    http_server._roles = {
        'fast_brain': 'openrouter/google/gemini-2.5-flash:free',
        'heavy_brain': 'openrouter/google/gemini-2.5-pro',
        'visual_brain': 'openrouter/openai/gpt-4o',
    }

    adapters = {
        'openai': _StaticAdapter('openai', [
            CompletionResponse(model='openai/gpt-4.1-mini', content='', error='HTTP 429: quota exceeded', status='error'),
        ]),
        'groq': _StaticAdapter('groq', [
            CompletionResponse(model='groq/llama-3.1-8b-instant', content='Hello. How can I assist you today?', status='success'),
        ]),
    }
    monkeypatch.setattr(http_server, 'get_adapter', lambda provider: adapters.get(provider))
    monkeypatch.setattr(http_server, 'get_adapter_for_model', lambda model: adapters.get(model.split('/', 1)[0]))

    decision = RouteDecision(
        target_model='google/gemini-2.5-flash',
        target_provider='google',
        confidence=0.85,
        decision_layers=['L1:heuristic'],
        reason='heuristic_quick',
    )
    response = CompletionResponse(
        model='google/gemini-2.5-flash',
        content='',
        error='HTTP 429: quota exceeded',
        status='error',
    )
    balance_report = BalanceReport(
        balances={
            'google': ProviderBalance(provider='google', has_credits=True, is_subscription=True, is_free_tier=True, source='estimated'),
            'openai': ProviderBalance(provider='openai', has_credits=True, is_subscription=True, source='estimated'),
            'groq': ProviderBalance(provider='groq', has_credits=True, is_free_tier=True, source='estimated'),
            'openrouter': ProviderBalance(provider='openrouter', has_credits=False, balance_usd=0.0, source='api'),
        },
        providers_with_credits=['google', 'openai', 'groq'],
    )

    decision, target_model, target_provider, adapter, response, exec_latency, failover_used = http_server._attempt_provider_failover(
        decision=decision,
        payload={'max_tokens': 50},
        messages=[{'role': 'user', 'content': 'hello'}],
        balance_report=balance_report,
        target_model='google/gemini-2.5-flash',
        target_provider='google',
        adapter=_StaticAdapter('google', []),
        response=response,
        exec_latency=0.0,
    )

    assert failover_used is True
    assert response.status == 'success'
    assert target_provider == 'groq'
    assert target_model == 'groq/llama-3.1-8b-instant'
    assert 'google/gemini-2.5-flash' in decision.fallback_chain
    assert 'verified_direct_fallback:groq' in decision.reason


def test_privacy_default_mode_allows_redacted_cloud_without_local_runtime():
    http_server._policy_engine = PolicyEngine({'pii_blocks_cloud': False, 'pii_prefer_local': False})
    http_server._roles = {'local_brain': ''}
    session = CanonicalSession(session_id='scenario-local-block')
    initial = PolicyResult(reason='')
    decision = RouteDecision(
        target_model='openai/gpt-4.1',
        target_provider='openai',
        confidence=0.91,
        decision_layers=['L1:heuristic'],
        reason='heuristic_analyst',
    )

    updated, model, provider, rerouted = http_server._maybe_force_local_privacy_route(
        decision=decision,
        initial_policy=initial,
        target_model='openai/gpt-4.1',
        target_provider='openai',
    )
    effective, reason = http_server._enforce_final_route_policy(
        session=session,
        initial_policy=initial,
        contains_pii=True,
        target_model=model,
        target_provider=provider,
        estimated_cost_usd=0.02,
    )

    assert rerouted is False
    assert updated.target_model == 'openai/gpt-4.1'
    assert effective.prefer_local is False
    assert effective.block_cloud is False
    assert reason == ''


def test_privacy_strict_mode_is_still_fail_closed_without_local_runtime():
    http_server._policy_engine = PolicyEngine({'pii_blocks_cloud': True, 'pii_prefer_local': True})
    http_server._roles = {'local_brain': ''}
    session = CanonicalSession(session_id='scenario-local-strict')
    initial = PolicyResult(block_cloud=True, prefer_local=True, reason='pii_detected_cloud_blocked')
    decision = RouteDecision(
        target_model='openai/gpt-4.1',
        target_provider='openai',
        confidence=0.91,
        decision_layers=['L1:heuristic'],
        reason='heuristic_analyst',
    )

    updated, model, provider, rerouted = http_server._maybe_force_local_privacy_route(
        decision=decision,
        initial_policy=initial,
        target_model='openai/gpt-4.1',
        target_provider='openai',
    )
    effective, reason = http_server._enforce_final_route_policy(
        session=session,
        initial_policy=initial,
        contains_pii=True,
        target_model=model,
        target_provider=provider,
        estimated_cost_usd=0.02,
    )

    assert rerouted is False
    assert effective.block_cloud is True
    assert reason == 'cloud_routing_blocked_by_policy'

def test_execution_timeout_failover_uses_verified_direct_provider(monkeypatch):
    optimizer = CostOptimizer({})
    optimizer.configure_provider_capabilities({
        'openai-codex': {'openai-codex/gpt-5.4'},
        'deepseek': {'deepseek/deepseek-reasoner', 'deepseek/deepseek-chat'},
    })
    http_server._cascade_router = SimpleNamespace(_cost_optimizer=optimizer)
    http_server._roles = {
        'fast_brain': 'minimax/minimax-01',
        'heavy_brain': 'qwen/qwen3-235b-a22b-thinking-2507',
        'visual_brain': 'openai/gpt-4o',
        'local_brain': 'lmstudio/qwen/qwen3-4b-thinking-2507',
    }

    adapters = {
        'openai-codex': _StaticAdapter('openai-codex', [
            CompletionResponse(model='openai-codex/gpt-5.4', content='', error='timeout', status='timeout'),
        ]),
        'deepseek': _StaticAdapter('deepseek', [
            CompletionResponse(model='deepseek/deepseek-reasoner', content='def add(a, b):\n    return a + b', status='success'),
        ]),
    }
    monkeypatch.setattr(http_server, 'get_adapter', lambda provider: adapters.get(provider))
    monkeypatch.setattr(http_server, 'get_adapter_for_model', lambda model: adapters.get(model.split('/', 1)[0]))

    decision = RouteDecision(
        target_model='openai-codex/gpt-5.4',
        target_provider='openai-codex',
        confidence=0.92,
        decision_layers=['L2:semantic:code_generation', 'L3:encoder:heavy_code'],
        reason='semantic_code_generation',
    )
    decision.model_preference = 'heavy'
    response = CompletionResponse(
        model='openai-codex/gpt-5.4',
        content='',
        error='timeout',
        status='timeout',
    )
    balance_report = BalanceReport(
        balances={
            'openai-codex': ProviderBalance(provider='openai-codex', has_credits=True, is_subscription=True, source='estimated'),
            'deepseek': ProviderBalance(provider='deepseek', has_credits=True, balance_usd=5.0, source='api'),
        },
        providers_with_credits=['openai-codex', 'deepseek'],
    )

    decision, target_model, target_provider, adapter, response, exec_latency, failover_used = http_server._attempt_provider_failover(
        decision=decision,
        payload={'max_tokens': 120},
        messages=[{'role': 'user', 'content': 'Write only Python code for a function add(a, b) with a unit test.'}],
        balance_report=balance_report,
        target_model='openai-codex/gpt-5.4',
        target_provider='openai-codex',
        adapter=adapters['openai-codex'],
        response=response,
        exec_latency=0.0,
    )

    assert failover_used is True
    assert response.status == 'success'
    assert target_provider == 'deepseek'
    assert target_model == 'deepseek/deepseek-reasoner'
    assert 'openai-codex/gpt-5.4' in decision.fallback_chain
