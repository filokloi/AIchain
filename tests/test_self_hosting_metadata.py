from tools.catalog_pipeline.self_hosting import build_self_hosted_model_index, derive_self_hosting_profile


def test_derive_self_hosting_profile_marks_open_weight_families() -> None:
    profile = derive_self_hosting_profile(
        'qwen/qwen3-30b-a3b',
        source_attribution={'catalog': ['openrouter'], 'intelligence': 'benchmark'},
        raw_metrics={'aa_quality': 88.0, 'lmsys_elo': 1320.0},
    )
    assert profile['self_hostable'] is True
    assert profile['open_weight'] is True
    assert profile['family'] == 'Qwen'
    assert profile['hosting_modes'] == ['cloud_api', 'self_hosted']
    assert 'huggingface' in profile['download_sources']
    assert 'Q4_K_M' in profile['quantizations_known']
    assert profile['hardware_profile_hint'] == '32gb_plus_recommended'
    assert profile['benchmark_evidence_sources'] == ['openrouter_catalog', 'curated_benchmark_map', 'artificial_analysis', 'lmsys_arena']


def test_derive_self_hosting_profile_keeps_closed_models_out_of_self_hosted_index() -> None:
    profile = derive_self_hosting_profile(
        'openai/gpt-4.1',
        source_attribution={'catalog': ['openrouter'], 'intelligence': 'benchmark'},
        raw_metrics={},
    )
    assert profile['self_hostable'] is False
    assert profile['open_weight'] is False
    assert profile['hosting_modes'] == ['cloud_api']
    assert profile['download_sources'] == []


def test_build_self_hosted_model_index_only_includes_self_hostable_entries() -> None:
    entries = [
        {
            'model': 'qwen/qwen2.5-7b-instruct',
            'display_name': 'Qwen 2.5 7B Instruct',
            'family_id': 'qwen/qwen2.5-7b-instruct',
            'provider': 'Qwen',
            'tier': 'FREE_FRONTIER',
            'task_label': 'CODING',
            'rank': 4,
            'raw_metrics': {'context_length': 131072},
            'metrics': {'intelligence': 74, 'speed': 76, 'stability': 80, 'cost': 0.0},
            'self_hosting': derive_self_hosting_profile(
                'qwen/qwen2.5-7b-instruct',
                source_attribution={'catalog': ['openrouter'], 'intelligence': 'benchmark'},
                raw_metrics={'aa_quality': 70.0},
            ),
        },
        {
            'model': 'openai/gpt-4.1',
            'display_name': 'GPT-4.1',
            'family_id': 'openai/gpt-4.1',
            'provider': 'OpenAI',
            'tier': 'HEAVY_HITTER',
            'task_label': 'GENERAL-CHAT',
            'rank': 1,
            'raw_metrics': {'context_length': 128000},
            'metrics': {'intelligence': 96, 'speed': 84, 'stability': 94, 'cost': 0.01},
            'self_hosting': derive_self_hosting_profile(
                'openai/gpt-4.1',
                source_attribution={'catalog': ['openrouter'], 'intelligence': 'benchmark'},
                raw_metrics={},
            ),
        },
    ]
    index = build_self_hosted_model_index(entries)
    assert index['total_models'] == 1
    assert index['family_breakdown'] == {'Qwen': 1}
    assert index['entries'][0]['model'] == 'qwen/qwen2.5-7b-instruct'
