#!/usr/bin/env python3
"""Install or remove the AIchain bridge script from the local OpenClaw control UI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aichaind.ui.openclaw_install import BRIDGE_TAG, inject_bridge, remove_bridge

DEFAULT_INDEX = Path.home() / 'AppData' / 'Roaming' / 'npm' / 'node_modules' / 'openclaw' / 'dist' / 'control-ui' / 'index.html'
DEFAULT_BACKUP = DEFAULT_INDEX.with_suffix(DEFAULT_INDEX.suffix + '.aichain.bak')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--index', type=Path, default=DEFAULT_INDEX)
    parser.add_argument('--backup', type=Path, default=DEFAULT_BACKUP)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()

    index = args.index.expanduser()
    backup = args.backup.expanduser()
    if not index.exists():
        raise SystemExit(f'OpenClaw control UI index not found: {index}')

    html = index.read_text(encoding='utf-8')
    if not backup.exists():
        backup.write_text(html, encoding='utf-8')

    if args.remove:
        updated, changed = remove_bridge(html)
        action = 'removed'
    else:
        updated, changed = inject_bridge(html)
        action = 'installed'

    if changed:
        index.write_text(updated, encoding='utf-8')

    print(json.dumps({
        'index': str(index),
        'backup': str(backup),
        'action': action,
        'changed': changed,
        'bridge_present': BRIDGE_TAG in updated,
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
