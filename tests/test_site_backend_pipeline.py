from __future__ import annotations

import pickle
from pathlib import Path


from tools.catalog_pipeline import pipeline as catalog_pipeline
from tools.catalog_pipeline.helper_ai.service import AIHelperService
from tools.catalog_pipeline.normalize.aliases import build_alias_registry, family_id_from_model_id
from tools.catalog_pipeline.normalize.merge import merge_catalog_sources
from tools.catalog_pipeline.pipeline import arbitrate_catalog
from tools.catalog_pipeline.rank.scoring import SCORING_DISPLAY_FORMULA, rank_catalog_entries
from tools.catalog_pipeline.sources import artificial_analysis as aa_source
from tools.catalog_pipeline.sources import lmsys as lmsys_source
from tools.catalog_pipeline.sources import openrouter as openrouter_source
from tools.catalog_pipeline.types import SourceHealth, SourceResult


class _FakeDataFrame:
    def __init__(self, rows):
        self._rows = rows

    def iterrows(self):
        for index, row in self._rows:
            yield index, row

class _FakeProvider:
    def __init__(self, name: str, payload=None, error: str | None = None):
        self.name = name
        self.payload = payload
        self.error = error
        self.api_key = 'test'

    @property
    def available(self) -> bool:
        return True

    def call_json(self, prompt: str):
        from tools.catalog_pipeline.types import HelperCallResult

        if self.error:
            return HelperCallResult(provider=self.name, ok=False, error=self.error)
        return HelperCallResult(provider=self.name, ok=True, payload=self.payload)


class _NullHelper:
    def resolve_aliases(self, aliases, known_families):
        return {}

    def enrich_tasks(self, model_cards):
        return {}

    def prioritize_free_models(self, candidates):
        return []

    def to_dict(self):
        return {'available_helpers': [], 'used_provider': None, 'fallback_used': False, 'failed_helpers': [], 'events': []}


def _source_result(
    name: str,
    records: list[dict],
    *,
    healthy: bool = True,
    status: str | None = None,
    warnings: list[str] | None = None,
    issues: list[str] | None = None,
) -> SourceResult:
    return SourceResult(
        name=name,
        records=records,
        health=SourceHealth(
            name=name,
            status=status or ('ok' if healthy else 'failed'),
            healthy=healthy,
            fetched_records=len(records),
            accepted_records=len(records),
            coverage=1.0 if records else 0.0,
            warnings=list(warnings or []),
            issues=list(issues or []),
        ),
    )


def test_openrouter_adapter_schema_validation_and_health(monkeypatch, tmp_path):
    payload = {
        'data': [
            {'id': 'openai/gpt-4o', 'name': 'GPT-4o', 'pricing': {'prompt': '0.1', 'completion': '0.2'}, 'context_length': 128000},
            {'id': '', 'name': 'broken', 'pricing': {}},
        ]
    }
    monkeypatch.setattr(openrouter_source, 'fetch_json', lambda *args, **kwargs: (payload, 12, None))
    result = openrouter_source.fetch_openrouter_source(None, snapshot_dirs={'run_dir': tmp_path, 'latest_dir': tmp_path})
    assert len(result.records) == 1
    assert result.health.status == 'degraded'
    assert result.health.accepted_records == 1


def test_lmsys_legacy_and_artificial_analysis_adapters_report_runtime_ready(monkeypatch, tmp_path):
    legacy_payload = {f'Model {idx}': 1300 + idx for idx in range(10)}
    monkeypatch.setattr(lmsys_source, 'fetch_json', lambda *args, **kwargs: (legacy_payload, 8, None))
    lmsys = lmsys_source.fetch_lmsys_source(snapshot_dirs={'run_dir': tmp_path, 'latest_dir': tmp_path})
    assert lmsys.records[0]['model_name'] == 'Model 0'
    assert lmsys.records[0]['metric_source'] == 'arena_elo_legacy_json'
    assert lmsys.health.status == 'ok'

    aa_payload = {
        'data': [
            {
                'name': f'Model {idx}',
                'slug': f'model-{idx}',
                'evaluations': {
                    'artificial_analysis_intelligence_index': 90 + idx,
                    'artificial_analysis_coding_index': 80 + idx,
                },
                'pricing': {'price_1m_blended_3_to_1': 0.25 + idx},
                'median_output_tokens_per_second': 200.0 + idx,
                'median_time_to_first_token_seconds': 0.5 + idx,
            }
            for idx in range(10)
        ]
    }
    monkeypatch.setattr(aa_source, 'fetch_json', lambda *args, **kwargs: (aa_payload, 9, None))
    aa = aa_source.fetch_artificial_analysis_source('test-key', snapshot_dirs={'run_dir': tmp_path, 'latest_dir': tmp_path})
    assert aa.records[0]['quality'] == 90.0
    assert aa.records[0]['speed'] == 200.0
    assert aa.records[0]['coding'] == 80.0
    assert aa.records[0]['metric_source'] == 'artificial_analysis_v2'
    assert aa.health.status == 'ok'


def test_lmsys_pickle_target_form_is_runtime_confirmed(monkeypatch, tmp_path):
    df = _FakeDataFrame(
        [
            (
                f'model-{idx}',
                {
                    'rating': 1466.2 - idx,
                    'variance': 5.8 + idx,
                    'rating_upper': 1470.9 - idx,
                    'rating_lower': 1461.5 - idx,
                    'num_battles': 35586 - idx,
                    'final_ranking': idx + 1,
                },
            )
            for idx in range(10)
        ]
    )
    payload = {'text': {'full': {'leaderboard_table_df': df}}}
    pkl_bytes = pickle.dumps(payload)

    def fake_fetch_json(url, *args, **kwargs):
        if url == lmsys_source.LMSYS_ARENA_URL:
            return None, 5, '404'
        if url == lmsys_source.LMSYS_SPACE_API:
            return {'siblings': [{'rfilename': 'elo_results_20250829.pkl'}, {'rfilename': 'leaderboard_table_20250804.csv'}]}, 4, None
        raise AssertionError(f'unexpected URL: {url}')

    monkeypatch.setattr(lmsys_source, 'fetch_json', fake_fetch_json)
    monkeypatch.setattr(lmsys_source, 'fetch_bytes', lambda *args, **kwargs: (pkl_bytes, 6, None))
    monkeypatch.setattr(lmsys_source, 'fetch_text', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('csv fallback should not run')))

    result = lmsys_source.fetch_lmsys_source(snapshot_dirs={'run_dir': tmp_path, 'latest_dir': tmp_path})
    assert len(result.records) == 10
    assert result.records[0]['metric_source'] == 'arena_elo_pickle'
    assert result.health.status == 'ok'
    op = catalog_pipeline._source_operational_status('lmsys', result.health.to_dict())
    assert op['mode'] == 'runtime_confirmed'
    assert op['runtime_confirmed'] is True


def test_lmsys_csv_arena_elo_fallback_is_target_form_not_reached(monkeypatch, tmp_path):
    def fake_fetch_json(url, *args, **kwargs):
        if url == lmsys_source.LMSYS_ARENA_URL:
            return None, 5, '404'
        if url == lmsys_source.LMSYS_SPACE_API:
            return {'siblings': [{'rfilename': 'leaderboard_table_20250804.csv'}]}, 4, None
        raise AssertionError(f'unexpected URL: {url}')

    monkeypatch.setattr(lmsys_source, 'fetch_json', fake_fetch_json)
    monkeypatch.setattr(lmsys_source, 'fetch_bytes', lambda *args, **kwargs: (None, 5, 'missing pickle'))
    monkeypatch.setattr(lmsys_source, 'fetch_text', lambda *args, **kwargs: ('Model,Arena Elo rating\nModel A,1321\n', 6, None))

    result = lmsys_source.fetch_lmsys_source(snapshot_dirs={'run_dir': tmp_path, 'latest_dir': tmp_path})
    assert result.records[0]['metric_source'] == 'arena_elo_csv'
    assert result.health.status == 'degraded'
    op = catalog_pipeline._source_operational_status('lmsys', result.health.to_dict())
    assert op['mode'] == 'target_form_not_reached'
    assert op['runtime_confirmed'] is False


def test_lmsys_csv_surrogate_is_explicit_degraded_fallback(monkeypatch, tmp_path):
    def fake_fetch_json(url, *args, **kwargs):
        if url == lmsys_source.LMSYS_ARENA_URL:
            return None, 5, '404'
        if url == lmsys_source.LMSYS_SPACE_API:
            return {'siblings': [{'rfilename': 'leaderboard_table_20250804.csv'}]}, 4, None
        raise AssertionError(f'unexpected URL: {url}')

    monkeypatch.setattr(lmsys_source, 'fetch_json', fake_fetch_json)
    monkeypatch.setattr(lmsys_source, 'fetch_bytes', lambda *args, **kwargs: (None, 5, 'missing pickle'))
    monkeypatch.setattr(lmsys_source, 'fetch_text', lambda *args, **kwargs: ('key,Model,MT-bench (score)\nmodel-a,Model A,8.5\n', 6, None))
    result = lmsys_source.fetch_lmsys_source(snapshot_dirs={'run_dir': tmp_path, 'latest_dir': tmp_path})
    assert result.records[0]['model_name'] == 'Model A'
    assert result.records[0]['metric_source'] == 'mt_bench_csv_surrogate'
    assert result.health.status == 'degraded'
    op = catalog_pipeline._source_operational_status('lmsys', result.health.to_dict())
    assert op['mode'] == 'degraded_fallback'
    assert op['runtime_confirmed'] is False


def test_artificial_analysis_missing_key_is_blocked_missing_credentials():
    result = aa_source.fetch_artificial_analysis_source(None)
    op = catalog_pipeline._source_operational_status('artificial_analysis', result.health.to_dict())
    assert op['mode'] == 'blocked_missing_credentials'
    assert op['blocked_reason'] == 'missing ARTIFICIAL_ANALYSIS_KEY'


def test_helper_service_uses_fallback_when_primary_fails():
    helper = AIHelperService(
        primary=_FakeProvider('gemini', error='quota exceeded'),
        fallback=_FakeProvider('groq', payload={'top_models': ['google/gemini-2.5-flash']}),
    )
    result = helper.prioritize_free_models([{'id': 'google/gemini-2.5-flash'}])
    assert result == ['google/gemini-2.5-flash']
    report = helper.to_dict()
    assert report['fallback_used'] is True
    assert report['used_provider'] == 'groq'
    assert 'gemini' in report['failed_helpers']


def test_alias_registry_and_merge_engine_resolve_conflicts_deterministically():
    openrouter_models = [
        {'id': 'openai/gpt-4o', 'name': 'GPT-4o', 'pricing': {'prompt': '0.000005', 'completion': '0.000015'}, 'context_length': 128000},
        {'id': 'openai/gpt-4o', 'name': 'GPT-4o duplicate', 'pricing': {'prompt': '0.1', 'completion': '0.2'}, 'context_length': 8192},
    ]
    registry = build_alias_registry(openrouter_models)
    aa_records = [{'model_name': 'GPT 4o', 'quality': 93.0, 'speed': 220.0}]
    lmsys_records = [{'model_name': 'GPT-4o', 'elo': 1330.0}]
    entries, diagnostics = merge_catalog_sources(openrouter_models, registry, aa_records, lmsys_records)
    assert diagnostics.deduped_catalog_duplicates == 1
    target = next(entry for entry in entries if entry['model'] == 'openai/gpt-4o')
    assert target['family_id'] == family_id_from_model_id('openai/gpt-4o')
    assert target['raw_metrics']['aa_quality'] == 93.0
    assert target['raw_metrics']['lmsys_elo'] == 1330.0
    assert target['source_attribution']['intelligence'] == 'benchmark'


def test_ranking_invariants_keep_raw_and_normalized_metrics_separate():
    entries = [
        {
            'model': 'google/gemini-2.5-flash',
            'raw_metrics': {'intelligence_base': 90, 'average_cost': 0.0, 'context_length': 1000000, 'stability_hint': 90, 'availability_hint': 92, 'aa_speed': 240.0, 'source_count': 2},
            'task_metadata': {'coverage_score': 80, 'primary': ['general_chat']},
            'helper_metadata': {},
        },
        {
            'model': 'anthropic/claude-sonnet-4',
            'raw_metrics': {'intelligence_base': 95, 'average_cost': 0.02, 'context_length': 200000, 'stability_hint': 94, 'availability_hint': 91, 'aa_speed': 180.0, 'source_count': 2},
            'task_metadata': {'coverage_score': 84, 'primary': ['reasoning']},
            'helper_metadata': {},
        },
    ]
    ranked, scoring = rank_catalog_entries(entries)
    assert ranked[0]['value_score'] >= ranked[1]['value_score']
    assert 'raw_metrics' in ranked[0]
    assert 'normalized_metrics' in ranked[0]
    assert scoring['display_formula'] == SCORING_DISPLAY_FORMULA


def test_pipeline_gracefully_degrades_without_secondary_sources_or_helpers(tmp_path):
    openrouter = _source_result('openrouter', [{'id': 'google/gemini-2.5-flash', 'name': 'Gemini Flash', 'pricing': {'prompt': 0, 'completion': 0}, 'context_length': 1000000}], healthy=True)
    lmsys = _source_result('lmsys', [], healthy=False, status='failed')
    aa = _source_result('artificial_analysis', [], healthy=False, status='failed', issues=['missing ARTIFICIAL_ANALYSIS_KEY for v2 API access'])
    table = arbitrate_catalog(
        injected_sources={'openrouter': openrouter, 'lmsys': lmsys, 'artificial_analysis': aa},
        injected_helper=_NullHelper(),
        snapshot_root=tmp_path,
        output_file=tmp_path / 'ai_routing_table.json',
    )
    assert table['system_status'] == 'DEGRADED'
    assert table['routing_hierarchy']
    assert table['helper_ai']['available_helpers'] == []
    assert table['public_artifact_readiness']['dashboard_switch_ready'] is False
    assert (tmp_path / 'latest' / 'pipeline_report.json').exists()


def test_pipeline_becomes_operational_with_runtime_confirmed_lmsys_target_form(tmp_path):
    openrouter = _source_result(
        'openrouter',
        [{'id': 'google/gemini-2.5-flash', 'name': 'Gemini Flash', 'pricing': {'prompt': 0, 'completion': 0}, 'context_length': 1000000}],
        healthy=True,
    )
    lmsys = _source_result(
        'lmsys',
        [{'model_name': 'Gemini 2.5 Pro', 'elo': 1466.2, 'metric_source': 'arena_elo_pickle'}],
        healthy=True,
        status='ok',
    )
    aa = _source_result(
        'artificial_analysis',
        [],
        healthy=False,
        status='failed',
        issues=['missing ARTIFICIAL_ANALYSIS_KEY for v2 API access'],
    )
    table = arbitrate_catalog(
        injected_sources={'openrouter': openrouter, 'lmsys': lmsys, 'artificial_analysis': aa},
        injected_helper=_NullHelper(),
        snapshot_root=tmp_path,
        output_file=tmp_path / 'ai_routing_table.json',
    )
    assert table['system_status'] == 'OPERATIONAL'
    assert table['operational_status']['sources']['lmsys']['runtime_confirmed'] is True
    assert table['operational_status']['sources']['artificial_analysis']['mode'] == 'blocked_missing_credentials'
    assert table['public_artifact_readiness']['dashboard_switch_ready'] is True


def test_pipeline_works_when_openrouter_catalog_is_down(tmp_path):
    openrouter = _source_result('openrouter', [], healthy=False, status='failed')
    lmsys = _source_result('lmsys', [], healthy=False, status='failed')
    aa = _source_result('artificial_analysis', [], healthy=False, status='failed')
    table = arbitrate_catalog(
        injected_sources={'openrouter': openrouter, 'lmsys': lmsys, 'artificial_analysis': aa},
        injected_helper=_NullHelper(),
        snapshot_root=tmp_path,
        output_file=tmp_path / 'ai_routing_table.json',
    )
    assert table['system_status'] == 'DEGRADED'
    assert any(entry['tier'] == 'OAUTH_BRIDGE' for entry in table['routing_hierarchy'])


def test_helper_alias_resolution_is_applied_in_pipeline(tmp_path):
    class _AliasHelper(_NullHelper):
        def resolve_aliases(self, aliases, known_families):
            return {'GPT4o Omni': 'openai/gpt-4o'}

        def to_dict(self):
            return {'available_helpers': ['stub'], 'used_provider': 'stub', 'fallback_used': False, 'failed_helpers': [], 'events': ['alias-resolution']}

    openrouter = _source_result('openrouter', [{'id': 'openai/gpt-4o', 'name': 'GPT-4o', 'pricing': {'prompt': 0.000005, 'completion': 0.000015}, 'context_length': 128000}], healthy=True)
    lmsys = _source_result('lmsys', [{'model_name': 'GPT4o Omni', 'elo': 1330.0}], healthy=True)
    aa = _source_result('artificial_analysis', [], healthy=False, status='failed')
    table = arbitrate_catalog(
        injected_sources={'openrouter': openrouter, 'lmsys': lmsys, 'artificial_analysis': aa},
        injected_helper=_AliasHelper(),
        snapshot_root=tmp_path,
        output_file=tmp_path / 'ai_routing_table.json',
    )
    assert table['merge_diagnostics']['helper_alias_resolutions']['GPT4o Omni'] == 'openai/gpt-4o'


def test_site_formula_matches_backend_constant():
    index_html = Path('C:/Users/filok/OneDrive/Desktop/AI chain for Open Claw envirement/index.html').read_text(encoding='utf-8')
    assert SCORING_DISPLAY_FORMULA in index_html






