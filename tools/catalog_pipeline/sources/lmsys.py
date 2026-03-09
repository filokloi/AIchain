from __future__ import annotations

import csv
import importlib
import io
import pickle
import re
from datetime import datetime, timezone
from typing import Any

from ..constants import LMSYS_ARENA_URL, LMSYS_SPACE_API
from ..snapshots import write_snapshot
from ..types import SourceResult
from .base import fetch_bytes, fetch_json, fetch_text, finalize_source_health


class _PlotlyPlaceholder:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, *args, **kwargs):
        return _PlotlyPlaceholder(*args, **kwargs)

    def __setstate__(self, state):
        self.state = state

    def __getattr__(self, name):
        return _PlotlyPlaceholder()


class _MixedUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str):
        if module.startswith('plotly'):
            return _PlotlyPlaceholder
        mod = importlib.import_module(module)
        return getattr(mod, name)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_records(payload: object) -> list[dict]:
    records: list[dict] = []
    if isinstance(payload, dict):
        for name, value in payload.items():
            elo = value.get('elo') if isinstance(value, dict) else value
            if isinstance(name, str) and isinstance(elo, (int, float)):
                records.append({'model_name': name, 'elo': float(elo), 'metric_source': 'arena_elo_legacy_json'})
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                name = item.get('model_name') or item.get('name') or item.get('model')
                elo = item.get('elo')
                if isinstance(name, str) and isinstance(elo, (int, float)):
                    records.append({'model_name': name, 'elo': float(elo), 'metric_source': 'arena_elo_legacy_json'})
    return records


def _discover_latest_artifacts() -> tuple[dict[str, str | None], list[str]]:
    payload, _latency_ms, error = fetch_json(LMSYS_SPACE_API, timeout=20)
    if error or not isinstance(payload, dict):
        return {'pkl': None, 'csv': None}, [f'metadata discovery failed: {error or "invalid payload"}']

    siblings = payload.get('siblings', [])
    pickle_candidates: list[str] = []
    csv_candidates: list[str] = []
    for item in siblings:
        if not isinstance(item, dict):
            continue
        filename = str(item.get('rfilename', ''))
        if re.fullmatch(r'elo_results_\d{8}\.pkl', filename):
            pickle_candidates.append(filename)
        elif re.fullmatch(r'leaderboard_table_\d{8}\.csv', filename):
            csv_candidates.append(filename)

    artifacts = {
        'pkl': None,
        'csv': None,
    }
    warnings: list[str] = []
    if pickle_candidates:
        latest = sorted(pickle_candidates)[-1]
        artifacts['pkl'] = f'https://huggingface.co/spaces/lmarena-ai/arena-leaderboard/resolve/main/{latest}'
    else:
        warnings.append('no elo_results pickle snapshot found in LMSYS metadata')

    if csv_candidates:
        latest = sorted(csv_candidates)[-1]
        artifacts['csv'] = f'https://huggingface.co/spaces/lmarena-ai/arena-leaderboard/resolve/main/{latest}'
    else:
        warnings.append('no leaderboard_table CSV snapshot found in LMSYS metadata')
    return artifacts, warnings


def _extract_records_from_pickle(data: bytes) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    try:
        payload = _MixedUnpickler(io.BytesIO(data)).load()
    except Exception as exc:
        return [], [f'pickle parse failed: {exc}']

    if not isinstance(payload, dict):
        return [], ['pickle payload was not a dict']

    text_section = payload.get('text')
    if not isinstance(text_section, dict):
        return [], ['pickle payload missing text leaderboard section']
    full_section = text_section.get('full')
    if not isinstance(full_section, dict):
        return [], ['pickle payload missing text/full leaderboard section']
    leaderboard = full_section.get('leaderboard_table_df')
    if leaderboard is None or not hasattr(leaderboard, 'iterrows'):
        return [], ['pickle payload missing usable leaderboard_table_df']

    records: list[dict] = []
    for index, row in leaderboard.iterrows():
        row_get = row.get if hasattr(row, 'get') else None
        model_name = None
        if callable(row_get):
            model_name = row_get('model_name') or row_get('model') or row_get('name')
        if not isinstance(model_name, str) or not model_name.strip():
            model_name = index if isinstance(index, str) else None
        rating = row_get('rating') if callable(row_get) else None
        if rating is None and callable(row_get):
            rating = row_get('elo')
        elo = _coerce_float(rating)
        if not isinstance(model_name, str) or elo is None:
            continue
        records.append({
            'model_name': model_name,
            'elo': elo,
            'variance': _coerce_float(row_get('variance') if callable(row_get) else None),
            'num_battles': _coerce_int(row_get('num_battles') if callable(row_get) else None),
            'metric_source': 'arena_elo_pickle',
        })

    if not records:
        warnings.append('pickle payload did not yield usable Arena Elo rows')
    return records, warnings


def _extract_records_from_csv(text: str) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    records: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if not isinstance(row, dict):
            continue
        model_name = row.get('Model') or row.get('key') or row.get('model')
        elo_value = row.get('Arena Elo rating') or row.get('Arena Elo') or row.get('Elo')
        metric_source = 'arena_elo_csv'
        if elo_value is None:
            mt_bench = row.get('MT-bench (score)') or row.get('MT-bench')
            if mt_bench is not None:
                try:
                    mt_value = float(mt_bench)
                    elo_value = 800 + mt_value * 60
                    metric_source = 'mt_bench_csv_surrogate'
                except ValueError:
                    elo_value = None
        try:
            elo_number = float(elo_value) if elo_value is not None else None
        except (TypeError, ValueError):
            elo_number = None
        if isinstance(model_name, str) and elo_number is not None:
            records.append({'model_name': model_name, 'elo': elo_number, 'metric_source': metric_source})

    metric_sources = {record.get('metric_source') for record in records}
    if 'mt_bench_csv_surrogate' in metric_sources:
        warnings.append('used MT-bench CSV surrogate because canonical Arena Elo sources were unavailable')
    elif 'arena_elo_csv' in metric_sources:
        warnings.append('used Arena Elo CSV fallback because canonical LMArena pickle snapshot was unavailable')
    return records, warnings


def fetch_lmsys_source(*, snapshot_dirs: dict | None = None) -> SourceResult:
    fetched_at = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []
    issues: list[str] = []
    raw_payload: Any = None
    records: list[dict] = []
    latency_ms: int | None = None

    payload, latency_ms, error = fetch_json(LMSYS_ARENA_URL, timeout=20)
    if error:
        issues.append(f'legacy JSON unavailable: {error}')
    else:
        records = _extract_records(payload)
        if records:
            raw_payload = {
                'source_url': LMSYS_ARENA_URL,
                'record_count': len(records),
                'metric_source': 'arena_elo_legacy_json',
            }

    artifacts, discovery_warnings = _discover_latest_artifacts()
    warnings.extend(discovery_warnings)

    if not records and artifacts.get('pkl'):
        pkl_bytes, pkl_latency_ms, pkl_error = fetch_bytes(artifacts['pkl'], timeout=30)
        latency_ms = pkl_latency_ms if pkl_latency_ms is not None else latency_ms
        if pkl_error:
            issues.append(f'pickle fallback unavailable: {pkl_error}')
        elif isinstance(pkl_bytes, bytes):
            records, pickle_warnings = _extract_records_from_pickle(pkl_bytes)
            warnings.extend(pickle_warnings)
            if records:
                raw_payload = {
                    'source_url': artifacts['pkl'],
                    'record_count': len(records),
                    'metric_source': 'arena_elo_pickle',
                }

    if not records and artifacts.get('csv'):
        csv_text, csv_latency_ms, csv_error = fetch_text(artifacts['csv'], timeout=30)
        latency_ms = csv_latency_ms if csv_latency_ms is not None else latency_ms
        if csv_error:
            issues.append(f'csv fallback unavailable: {csv_error}')
        elif isinstance(csv_text, str):
            records, csv_warnings = _extract_records_from_csv(csv_text)
            warnings.extend(csv_warnings)
            if records:
                metric_source = records[0].get('metric_source')
                raw_payload = {
                    'source_url': artifacts['csv'],
                    'record_count': len(records),
                    'metric_source': metric_source,
                }
        else:
            issues.append('csv fallback returned non-text payload')

    if raw_payload is None:
        raw_payload = {'issues': issues, 'warnings': warnings}

    snapshot_path = write_snapshot('lmsys', raw_payload, snapshot_dirs) if snapshot_dirs else None
    health = finalize_source_health(
        'lmsys',
        fetched_records=len(records),
        accepted_records=len(records),
        latency_ms=latency_ms,
        issues=issues,
        warnings=warnings,
        snapshot_path=snapshot_path,
    )

    metric_sources = {record.get('metric_source') for record in records}
    if 'mt_bench_csv_surrogate' in metric_sources or 'arena_elo_csv' in metric_sources:
        health.status = 'degraded' if health.status == 'ok' else health.status

    return SourceResult(name='lmsys', records=records, health=health, raw_payload=raw_payload, fetched_at=fetched_at)
