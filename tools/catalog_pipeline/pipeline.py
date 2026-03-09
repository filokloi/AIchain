from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import OUTPUT_FILE, OAUTH_BRIDGES
from .helper_ai import build_helper_service
from .normalize import build_alias_registry, merge_catalog_sources
from .rank import rank_catalog_entries
from .snapshots import prepare_snapshot_dirs, write_pipeline_report
from .sources import fetch_artificial_analysis_source, fetch_lmsys_source, fetch_openrouter_source


def _derive_status(
    source_health: dict[str, dict[str, Any]],
    source_operational: dict[str, dict[str, Any]],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    openrouter = source_health.get('openrouter', {})
    if not openrouter.get('healthy'):
        reasons.append('openrouter catalog degraded or unavailable')

    secondary_confirmed = any(
        bool(source_operational.get(name, {}).get('runtime_confirmed'))
        for name in ('lmsys', 'artificial_analysis')
    )
    if not secondary_confirmed:
        reasons.append('no secondary benchmark source is runtime-confirmed in target form')

    if openrouter.get('accepted_records', 0) <= 0:
        return 'DEGRADED', reasons or ['openrouter unavailable; bridge-only fallback catalog']
    return ('DEGRADED' if reasons else 'OPERATIONAL'), reasons


def _source_operational_status(name: str, info: dict[str, Any]) -> dict[str, Any]:
    issues = info.get('issues', []) or []
    warnings = info.get('warnings', []) or []
    healthy = bool(info.get('healthy'))
    accepted = int(info.get('accepted_records') or 0)
    status = info.get('status') or 'unknown'
    lowered_warnings = [str(warning).lower() for warning in warnings]

    blocked_reason = None
    mode = 'failed_runtime'
    runtime_confirmed = False
    target_state = 'runtime_confirmed'
    acceptance = 'source returns valid normalized records without fallback or credential blocks'

    if name == 'artificial_analysis':
        target_state = 'runtime_confirmed_v2_api'
        acceptance = 'ARTIFICIAL_ANALYSIS_KEY configured and v2 API returns valid quality/speed rows'
        if any('missing ARTIFICIAL_ANALYSIS_KEY' in issue for issue in issues):
            mode = 'blocked_missing_credentials'
            blocked_reason = 'missing ARTIFICIAL_ANALYSIS_KEY'
        elif healthy and accepted > 0:
            mode = 'runtime_confirmed'
            runtime_confirmed = True
        elif accepted > 0:
            mode = 'target_form_not_reached'
    elif name == 'lmsys':
        target_state = 'runtime_confirmed_arena_elo_pickle'
        acceptance = 'source returns Arena ELO from the LMArena pickle feed, not a CSV fallback or MT-bench surrogate'
        if healthy and accepted > 0:
            if any('surrogate' in warning for warning in lowered_warnings):
                mode = 'degraded_fallback'
            elif any('csv fallback' in warning for warning in lowered_warnings):
                mode = 'target_form_not_reached'
            else:
                mode = 'runtime_confirmed'
                runtime_confirmed = True
        elif accepted > 0:
            if any('surrogate' in warning for warning in lowered_warnings):
                mode = 'degraded_fallback'
            else:
                mode = 'target_form_not_reached'
    else:
        if healthy and accepted > 0:
            mode = 'runtime_confirmed'
            runtime_confirmed = True
        elif accepted > 0:
            mode = 'degraded_runtime'

    return {
        'implemented_in_code': True,
        'runtime_confirmed': runtime_confirmed,
        'mode': mode,
        'raw_status': status,
        'target_state': target_state,
        'acceptance_criteria': acceptance,
        'blocked_reason': blocked_reason,
        'issues': list(issues),
        'warnings': list(warnings),
    }


def _helper_operational_status(helper_report: dict[str, Any]) -> dict[str, Any]:
    statuses = helper_report.get('provider_statuses', {}) if isinstance(helper_report, dict) else {}
    result: dict[str, Any] = {}
    for name, info in statuses.items():
        if not isinstance(info, dict):
            continue
        mode = info.get('status') or 'unknown'
        target_state = 'runtime_confirmed_fallback' if info.get('role') == 'fallback' else 'runtime_confirmed_primary'
        acceptance = (
            'provider successfully serves at least one helper request after a primary-helper failure'
            if info.get('role') == 'fallback'
            else 'provider successfully serves at least one helper request in the live pipeline'
        )
        result[name] = {
            'implemented_in_code': True,
            'runtime_confirmed': bool(info.get('runtime_confirmed')),
            'mode': mode,
            'role': info.get('role'),
            'configured': bool(info.get('configured')),
            'blocked_reason': info.get('blocked_reason'),
            'target_state': target_state,
            'acceptance_criteria': acceptance,
            'used_as_fallback': bool(info.get('used_as_fallback')),
            'successes': int(info.get('successes') or 0),
            'failures': int(info.get('failures') or 0),
            'last_error': info.get('last_error'),
        }
    return result


def _evaluate_public_artifact_readiness(
    source_operational: dict[str, Any],
    helper_operational: dict[str, Any],
    scoring: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    blockers: list[str] = []
    conditions = {
        'manifest_scoring_present': bool(scoring.get('display_formula')),
        'openrouter_runtime_confirmed': bool(source_operational.get('openrouter', {}).get('runtime_confirmed')),
        'secondary_benchmark_confirmed': any(
            bool(source_operational.get(name, {}).get('runtime_confirmed'))
            for name in ('lmsys', 'artificial_analysis')
        ),
        'no_critical_blocked_sources': not any(
            source_operational.get(name, {}).get('mode') == 'blocked_missing_credentials'
            for name in ('openrouter',)
        ),
        'global_plane_not_degraded': status == 'OPERATIONAL',
    }

    if not conditions['manifest_scoring_present']:
        blockers.append('scoring metadata missing from public artifact')
    if not conditions['openrouter_runtime_confirmed']:
        blockers.append('OpenRouter catalog is not runtime-confirmed')
    if not conditions['secondary_benchmark_confirmed']:
        blockers.append('no secondary benchmark source is runtime-confirmed in target form')
    if not conditions['global_plane_not_degraded']:
        blockers.append('global control plane is still operating in degraded mode')

    recommended_state = 'hold_legacy_dashboard_view' if blockers else 'safe_to_switch_dashboard_to_canonical_artifact'
    return {
        'dashboard_switch_ready': not blockers,
        'recommended_state': recommended_state,
        'conditions': conditions,
        'blockers': blockers,
        'next_acceptance_step': (
            'Confirm at least one secondary benchmark source in target runtime form and keep the control plane OPERATIONAL before switching the dashboard'
            if blockers else 'Dashboard can safely move to canonical public artifact'
        ),
    }


def _free_candidate_cards(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards = []
    for entry in entries:
        avg_cost = float(entry.get('raw_metrics', {}).get('average_cost') or 0.0)
        if avg_cost <= 0 or entry['model'] in OAUTH_BRIDGES:
            cards.append({
                'id': entry['model'],
                'family_id': entry.get('family_id'),
                'context_length': entry.get('raw_metrics', {}).get('context_length'),
                'task_primary': entry.get('task_metadata', {}).get('primary', []),
            })
    return cards


def _task_enrichment_candidates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for entry in entries:
        confidence = float(entry.get('task_metadata', {}).get('confidence') or 0.0)
        if confidence >= 0.8:
            continue
        cards.append({
            'model': entry['model'],
            'display_name': entry.get('display_name'),
            'context_length': entry.get('raw_metrics', {}).get('context_length'),
            'source_attribution': entry.get('source_attribution', {}),
        })
    return cards


def arbitrate_catalog(
    *,
    openrouter_key: str | None = None,
    gemini_key: str | None = None,
    groq_key: str | None = None,
    artificial_analysis_key: str | None = None,
    output_file: Path | None = None,
    snapshot_root: Path | None = None,
    injected_sources: dict[str, Any] | None = None,
    injected_helper: Any | None = None,
) -> dict[str, Any]:
    dirs = prepare_snapshot_dirs(snapshot_root)
    sources = injected_sources or {
        'openrouter': fetch_openrouter_source(openrouter_key, snapshot_dirs=dirs),
        'lmsys': fetch_lmsys_source(snapshot_dirs=dirs),
        'artificial_analysis': fetch_artificial_analysis_source(artificial_analysis_key, snapshot_dirs=dirs),
    }
    helper_service = injected_helper or build_helper_service(gemini_key, groq_key)

    source_health = {name: result.health.to_dict() for name, result in sources.items()}
    source_operational = {name: _source_operational_status(name, info) for name, info in source_health.items()}

    catalog_models = list(sources['openrouter'].records)
    if not catalog_models:
        catalog_models = [
            {'id': model_id, 'name': model_id, 'context_length': 128000, 'pricing': {'prompt': 0, 'completion': 0}}
            for model_id in sorted(OAUTH_BRIDGES)
        ]

    alias_registry = build_alias_registry(catalog_models)
    unresolved: list[str] = []
    for result in (sources['lmsys'], sources['artificial_analysis']):
        for record in result.records:
            model_name = str(record.get('model_name', '')).strip()
            if model_name and not alias_registry.resolve(model_name):
                unresolved.append(model_name)
    helper_alias_map = helper_service.resolve_aliases(sorted(set(unresolved)), alias_registry.known_families()) if helper_service else {}

    merged_entries, merge_diagnostics = merge_catalog_sources(
        catalog_models,
        alias_registry,
        list(sources['artificial_analysis'].records),
        list(sources['lmsys'].records),
        helper_alias_map=helper_alias_map,
    )

    helper_task_map = helper_service.enrich_tasks(_task_enrichment_candidates(merged_entries)) if helper_service else {}
    if helper_task_map:
        merged_entries, merge_diagnostics = merge_catalog_sources(
            catalog_models,
            alias_registry,
            list(sources['artificial_analysis'].records),
            list(sources['lmsys'].records),
            helper_alias_map=helper_alias_map,
            helper_task_map=helper_task_map,
        )

    promo_kings = helper_service.prioritize_free_models(_free_candidate_cards(merged_entries)) if helper_service else []
    helper_report = helper_service.to_dict() if helper_service else {}
    ranked_entries, scoring = rank_catalog_entries(merged_entries, promo_kings=promo_kings)
    heavy_hitter = max(ranked_entries, key=lambda entry: entry.get('metrics', {}).get('intelligence', 0)) if ranked_entries else None
    helper_operational = _helper_operational_status(helper_report)
    status, degradation_reasons = _derive_status(source_health, source_operational)
    public_artifact_readiness = _evaluate_public_artifact_readiness(source_operational, helper_operational, scoring, status)

    routing_table = {
        'system_status': status,
        'scope': 'GLOBAL_NON_DISCRIMINATORY',
        'version': '4.1-control-plane',
        'philosophy': 'Maximum Intelligence, Speed, and Stability at the Lowest Sustainable Cost.',
        'last_synopsis': datetime.now(timezone.utc).isoformat(),
        'data_sources': {
            'openrouter': len(sources['openrouter'].records),
            'lmsys_arena': len(sources['lmsys'].records),
            'artificial_analysis': len(sources['artificial_analysis'].records),
        },
        'source_health': source_health,
        'degradation_reasons': degradation_reasons,
        'helper_ai': helper_report,
        'operational_status': {
            'sources': source_operational,
            'helper_ai': helper_operational,
        },
        'public_artifact_readiness': public_artifact_readiness,
        'scoring': scoring,
        'merge_diagnostics': merge_diagnostics.to_dict(),
        'total_models_analyzed': len(ranked_entries),
        'live_promos': promo_kings,
        'tier_breakdown': {
            'OAUTH_BRIDGE': sum(1 for entry in ranked_entries if entry['tier'] == 'OAUTH_BRIDGE'),
            'FREE_FRONTIER': sum(1 for entry in ranked_entries if entry['tier'] == 'FREE_FRONTIER'),
            'HEAVY_HITTER': sum(1 for entry in ranked_entries if entry['tier'] == 'HEAVY_HITTER'),
        },
        'heavy_hitter': {
            'model': heavy_hitter['model'] if heavy_hitter else 'N/A',
            'intelligence': heavy_hitter['metrics']['intelligence'] if heavy_hitter else 0,
            'note': 'Global rescue model — use ONLY when lower-cost or free models fail',
        },
        'routing_hierarchy': ranked_entries,
        'canonical_public_artifact': {
            'target': 'catalog_manifest.json',
            'legacy_view': 'ai_routing_table.json',
            'migration_state': public_artifact_readiness['recommended_state'],
        },
    }

    destination = output_file if output_file is not None else (None if injected_sources is not None else OUTPUT_FILE)
    report = {
        'source_health': source_health,
        'degradation_reasons': degradation_reasons,
        'helper_ai': helper_report,
        'operational_status': routing_table['operational_status'],
        'public_artifact_readiness': public_artifact_readiness,
        'merge_diagnostics': routing_table['merge_diagnostics'],
        'output_file': str(destination) if destination is not None else None,
    }
    write_pipeline_report(report, dirs)

    if destination is not None:
        destination.write_text(json.dumps(routing_table, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return routing_table
