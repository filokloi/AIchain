#!/usr/bin/env python3
"""Tests for AIchain OpenClaw UI bridge endpoints and state."""

import io
import json
import tempfile
from pathlib import Path

import aichaind.transport.http_server as http_server
from aichaind.core.session import CanonicalSession, SessionStore


class _Auth:
    def __init__(self, token='token'):
        self._current_token = token
        self.is_active = True

    def validate(self, token: str) -> bool:
        return token == self._current_token


class _AccessLayer:
    def summary(self):
        return {
            'openai-codex': {
                'selected_method': 'oauth',
                'status': 'runtime_confirmed',
                'runtime_confirmed': True,
                'target_form_reached': True,
                'billing_basis': 'subscription',
                'quota_visibility': 'ui_only',
                'preferred_model': 'openai-codex/gpt-5.4',
                'verified_models': ['openai-codex/gpt-5.4'],
                'target_model': 'openai-codex/gpt-5.4',
            },
            'deepseek': {
                'selected_method': 'api_key',
                'status': 'runtime_confirmed',
                'runtime_confirmed': True,
                'target_form_reached': True,
                'billing_basis': 'api_metered',
                'quota_visibility': 'machine_readable',
            },
            'lmstudio': {
                'selected_method': 'local',
                'status': 'runtime_confirmed',
                'runtime_confirmed': True,
                'target_form_reached': True,
                'billing_basis': 'local_runtime',
                'quota_visibility': 'n/a',
            },
        }


def test_ui_cors_headers_accepts_openclaw_dashboard_origin():
    headers = http_server._ui_cors_headers('http://127.0.0.1:18789')
    assert headers['Access-Control-Allow-Origin'] == 'http://127.0.0.1:18789'
    assert 'X-AIchain-Token' in headers['Access-Control-Allow-Headers']


def test_ui_cors_headers_rejects_untrusted_origin():
    assert http_server._ui_cors_headers('https://example.com') == {}


def test_validate_ui_request_accepts_trusted_origin_without_token():
    handler = object.__new__(http_server.AichainDHandler)
    handler.headers = {
        'Origin': 'http://127.0.0.1:18789',
    }
    handler.send_error = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("send_error should not be called"))
    http_server._auth_manager = _Auth('secret-token')

    ok, headers = http_server.AichainDHandler._validate_ui_request(handler, 'http://127.0.0.1:18789')

    assert ok is True
    assert headers['Access-Control-Allow-Origin'] == 'http://127.0.0.1:18789'


def test_validate_ui_request_accepts_trusted_referer_without_token():
    handler = object.__new__(http_server.AichainDHandler)
    handler.headers = {
        'Origin': '',
        'Referer': 'http://127.0.0.1:8080/ui/panel?session_id=openclaw-default',
    }
    handler.send_error = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("send_error should not be called"))
    http_server._auth_manager = _Auth('secret-token')

    ok, headers = http_server.AichainDHandler._validate_ui_request(handler, '')

    assert ok is True
    assert headers == {}


def test_get_ui_session_uses_default_openclaw_session_id():
    with tempfile.TemporaryDirectory() as tmp:
        http_server._session_store = SessionStore(Path(tmp))
        session = http_server._get_ui_session('')
        assert session.session_id == http_server.DEFAULT_OPENCLAW_SESSION_ID
        loaded = http_server._session_store.load(http_server.DEFAULT_OPENCLAW_SESSION_ID)
        assert loaded is not None


def test_build_ui_control_state_exposes_recommended_route_and_model_options():
    http_server._provider_access_layer = _AccessLayer()
    http_server._roles = {
        'fast_brain': 'deepseek/deepseek-chat',
        'heavy_brain': 'openai-codex/gpt-5.4',
        'local_brain': 'lmstudio/qwen/qwen3-4b-thinking-2507',
    }
    http_server._local_profile_store = None

    session = CanonicalSession(session_id='sess-ui', routing_mode='auto', routing_preference='balanced')
    state = http_server._build_ui_control_state(session)

    assert state['recommended_current']['model'] == 'openai-codex/gpt-5.4'
    assert state['why_this_route']['summary']
    assert state['savings_summary']['headline']
    models = {item['model']: item for item in state['model_options']}
    assert 'openai-codex/gpt-5.4' in models
    assert 'Premium' in models['openai-codex/gpt-5.4']['badges']
    assert 'lmstudio/qwen/qwen3-4b-thinking-2507' in models
    assert models['openai-codex/gpt-5.4']['group'] == 'premium_access'


def test_build_ui_savings_summary_humanizes_missing_provider_metadata():
    summary = http_server._build_ui_savings_summary(
        {'provider': 'minimax', 'effective_cost_label': ''},
        {},
    )

    assert summary['kind'] == 'catalog'
    assert summary['cost_mode_label'] == 'catalog-ranked route'
    assert summary['quota_visibility_label'] == 'provider-specific or not machine-readable yet'
    assert summary['fallback_label'] == 'Automatic fallback to the next ranked runtime-confirmed route'
    assert summary['status_label'] == 'route metadata pending'


def test_ui_control_endpoint_updates_session_lock():
    with tempfile.TemporaryDirectory() as tmp:
        http_server._session_store = SessionStore(Path(tmp))
        http_server._auth_manager = _Auth('secret-token')
        http_server._provider_access_layer = _AccessLayer()
        http_server._roles = {'local_brain': 'lmstudio/qwen/qwen3-4b-thinking-2507'}
        captured = {}

        handler = object.__new__(http_server.AichainDHandler)
        handler.headers = {
            'Origin': 'http://127.0.0.1:18789',
            'X-AIchain-Token': 'secret-token',
            'Content-Length': '115',
        }
        payload = {
            'session_id': 'sess-ui-control',
            'mode': 'manual',
            'model': 'openai-codex/gpt-5.4',
            'provider': 'openai-codex',
            'persist_for_session': True,
        }
        encoded = json.dumps(payload).encode('utf-8')
        handler.headers['Content-Length'] = str(len(encoded))
        handler.rfile = io.BytesIO(encoded)
        handler._send_json = lambda status, data, extra_headers=None: captured.update({'status': status, 'data': data, 'headers': extra_headers or {}})
        handler.send_error = lambda status, message: captured.update({'status': status, 'data': {'error': message}})

        http_server.AichainDHandler._handle_ui_control(handler)

        assert captured['status'] == 200
        assert captured['data']['session']['routing_mode'] == 'manual'
        assert captured['data']['session']['locked_model'] == 'openai-codex/gpt-5.4'
        loaded = http_server._session_store.load('sess-ui-control')
        assert loaded is not None
        assert loaded.locked_model == 'openai-codex/gpt-5.4'


def test_ui_bridge_script_points_to_companion_panel():
    handler = object.__new__(http_server.AichainDHandler)
    http_server._auth_manager = _Auth('ui-token')
    captured = {}
    handler._send_text = lambda status, body, content_type, extra_headers=None: captured.update({'status': status, 'body': body, 'content_type': content_type, 'headers': extra_headers or {}})

    http_server.AichainDHandler._handle_ui_openclaw_bridge(handler)

    assert captured['status'] == 200
    assert '/ui/panel' in captured['body']
    assert 'Open Panel' in captured['body']
    assert 'aichain-chip' in captured['body']
    assert 'aichain-popover' in captured['body']
    assert 'Thinking…' in captured['body']
    assert 'scheduleRefresh' in captured['body']
    assert 'searchParams.get("session")' in captured['body']
    assert 'localStorage.setItem("aichain.openclaw.sessionId"' in captured['body']
    assert 'X-AIchain-Token' not in captured['body']


def test_ui_panel_endpoint_serves_companion_html():
    handler = object.__new__(http_server.AichainDHandler)
    http_server._auth_manager = _Auth('ui-token')
    captured = {}
    handler._send_text = lambda status, body, content_type, extra_headers=None: captured.update({'status': status, 'body': body, 'content_type': content_type, 'headers': extra_headers or {}})

    http_server.AichainDHandler._handle_ui_panel(handler)

    assert captured['status'] == 200
    assert captured['content_type'].startswith('text/html')
    assert 'AIchain Control Panel' in captured['body']
    assert 'Provider Access & Limits' in captured['body']
    assert 'Model Picker' in captured['body']
    assert 'Why This Model' in captured['body']
    assert 'Savings & Limits' in captured['body']
    assert 'request in progress' in captured['body']
    assert 'scheduleRefresh' in captured['body']
    assert 'X-AIchain-Token' not in captured['body']


def test_ui_control_state_exposes_running_request_state():
    http_server._provider_access_layer = _AccessLayer()
    http_server._roles = {'heavy_brain': 'openai-codex/gpt-5.4'}

    session = CanonicalSession(
        session_id='sess-running',
        routing_mode='auto',
        routing_preference='balanced',
        request_status='running',
        request_label='Thinking…',
    )

    state = http_server._build_ui_control_state(session)

    assert state['session']['request_status'] == 'running'
    assert state['session']['request_label'] == 'Thinking…'
