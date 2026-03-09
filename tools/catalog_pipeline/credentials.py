from __future__ import annotations

import json
import os
from pathlib import Path

OPENCLAW_CONFIG = Path.home() / '.openclaw' / 'openclaw.json'


def load_openclaw_env_vars(config_path: Path | None = None) -> dict[str, str]:
    path = config_path or OPENCLAW_CONFIG
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    env_vars = data.get('env', {}).get('vars', {})
    if not isinstance(env_vars, dict):
        return {}
    return {str(key): str(value) for key, value in env_vars.items() if value and not str(value).startswith('${')}


def resolve_credential(*var_names: str, config_path: Path | None = None) -> str:
    for name in var_names:
        value = os.environ.get(name, '')
        if value:
            return value
    env_vars = load_openclaw_env_vars(config_path)
    for name in var_names:
        value = env_vars.get(name, '')
        if value:
            return value
    return ''
