#!/usr/bin/env python3
"""Tests for the live task-fit runtime verifier helpers."""

from tools.verify_task_fit_runtime import classify_provider, verify_case


def test_classify_provider_marks_local_runtimes_as_local():
    assert classify_provider('lmstudio') == 'local'
    assert classify_provider('ollama') == 'local'
    assert classify_provider('deepseek') == 'cloud'


def test_verify_case_reports_provider_class_and_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                'choices': [{'message': {'content': 'ok'}}],
                '_aichaind': {
                    'routed_provider': 'deepseek',
                    'routed_model': 'deepseek/deepseek-chat',
                    'route_layers': ['L1:heuristic'],
                },
            }

    monkeypatch.setattr('tools.verify_task_fit_runtime.requests.post', lambda *args, **kwargs: FakeResponse())
    result = verify_case('http://127.0.0.1:8080', 'token', {
        'name': 'chat',
        'prompt': 'Say exactly CLOUD_OK and nothing else.',
        'max_tokens': 20,
        'expected_provider_class': 'cloud',
    })

    assert result.ok is True
    assert result.provider_class == 'cloud'
    assert result.routed_model == 'deepseek/deepseek-chat'


def test_verify_case_flags_wrong_provider_class(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                'choices': [{'message': {'content': 'ok'}}],
                '_aichaind': {
                    'routed_provider': 'lmstudio',
                    'routed_model': 'lmstudio/qwen/qwen3-4b-thinking-2507',
                    'route_layers': ['L2:semantic:code_generation'],
                },
            }

    monkeypatch.setattr('tools.verify_task_fit_runtime.requests.post', lambda *args, **kwargs: FakeResponse())
    result = verify_case('http://127.0.0.1:8080', 'token', {
        'name': 'structured',
        'prompt': 'Return JSON',
        'max_tokens': 20,
        'expected_provider_class': 'cloud',
    })

    assert result.ok is False
    assert result.provider_class == 'local'
