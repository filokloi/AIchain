#!/usr/bin/env python3
"""Helpers for injecting the AIchain bridge into the OpenClaw control UI."""

from __future__ import annotations

BRIDGE_TAG = '<script src="http://127.0.0.1:8080/ui/openclaw-bridge.js"></script>'


def inject_bridge(html: str) -> tuple[str, bool]:
    text = str(html or "")
    if BRIDGE_TAG in text:
        return text, False
    if '</body>' in text:
        return text.replace('</body>', f'{BRIDGE_TAG}\n</body>', 1), True
    return f'{text.rstrip()}\n{BRIDGE_TAG}\n', True


def remove_bridge(html: str) -> tuple[str, bool]:
    text = str(html or "")
    if BRIDGE_TAG not in text:
        return text, False
    return text.replace(BRIDGE_TAG + '\n', '', 1).replace(BRIDGE_TAG, '', 1), True
