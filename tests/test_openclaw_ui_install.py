#!/usr/bin/env python3

from pathlib import Path
import subprocess
import sys

from aichaind.ui.openclaw_install import BRIDGE_TAG, inject_bridge, remove_bridge


def test_inject_bridge_adds_tag_before_body():
    html, changed = inject_bridge('<html><body><div>OpenClaw</div></body></html>')
    assert changed is True
    assert BRIDGE_TAG in html
    assert html.index(BRIDGE_TAG) < html.index('</body>')


def test_inject_bridge_is_idempotent():
    first, _ = inject_bridge('<html><body></body></html>')
    second, changed = inject_bridge(first)
    assert changed is False
    assert second.count(BRIDGE_TAG) == 1


def test_remove_bridge_removes_tag():
    html, _ = inject_bridge('<html><body></body></html>')
    cleaned, changed = remove_bridge(html)
    assert changed is True
    assert BRIDGE_TAG not in cleaned


def test_install_script_round_trip(tmp_path: Path):
    index = tmp_path / 'index.html'
    backup = tmp_path / 'index.html.aichain.bak'
    index.write_text('<html><body><div>OpenClaw</div></body></html>', encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'tools' / 'install_openclaw_ui_bridge.py'

    subprocess.run([
        sys.executable,
        str(script),
        '--index', str(index),
        '--backup', str(backup),
    ], check=True)
    installed = index.read_text(encoding='utf-8')
    assert BRIDGE_TAG in installed
    assert backup.exists()

    subprocess.run([
        sys.executable,
        str(script),
        '--index', str(index),
        '--backup', str(backup),
        '--remove',
    ], check=True)
    removed = index.read_text(encoding='utf-8')
    assert BRIDGE_TAG not in removed
