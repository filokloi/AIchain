#!/usr/bin/env python3
"""Profile the active local_execution runtime and persist local model metrics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aichaind.core.state_machine import get_paths, load_config
from aichaind.providers.local_profile import LocalProfileStore, profile_local_model
from aichaind.providers.local_runtime import resolve_local_execution


def main() -> int:
    cfg = load_config(REPO_ROOT / 'config' / 'default.json')
    paths = get_paths(cfg)
    local_cfg = cfg.get('local_execution', {}) if isinstance(cfg, dict) else {}
    resolution = resolve_local_execution(local_cfg, timeout=3.0, detect_when_disabled=True)
    if resolution.status != 'runtime_confirmed':
        payload = {
            'status': 'not_profiled',
            'reason': resolution.reason,
            'resolution_status': resolution.status,
            'provider': resolution.provider,
            'model': resolution.model,
            'base_url': resolution.base_url,
            'probes': [probe.to_dict() for probe in resolution.probes],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1

    store = LocalProfileStore(paths['local_profile_file'])
    profile = profile_local_model(resolution.provider, resolution.model, resolution.base_url, timeout=60.0)
    store.upsert(profile)
    payload = {
        'status': 'profiled',
        'store_path': str(paths['local_profile_file']),
        'profile': profile.to_dict(),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if profile.runtime_confirmed else 1


if __name__ == '__main__':
    raise SystemExit(main())
