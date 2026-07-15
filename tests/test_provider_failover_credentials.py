"""Live Hermes E2E (2026-07-15) regression: Google 'Please pass a valid API
key' (subscription detected, key app-only/stale) returned 502 with NO
failover although OpenRouter was authenticated with 342 models."""
from types import SimpleNamespace

from aichaind.transport.http_server import (_provider_runtime_failure_reason,
                                            _should_retry_provider_error)


def _resp(error, status="error"):
    return SimpleNamespace(status=status, error=error)

GOOGLE_400 = ('HTTP 400: [{\n  "error": {\n    "code": 400,\n    "message": '
              '"Please pass a valid API key",\n    "status": "INVALID_ARGUMENT"\n  }\n}\n]')


def test_invalid_api_key_is_retryable():
    assert _should_retry_provider_error(_resp(GOOGLE_400))
    assert _should_retry_provider_error(_resp("401 Unauthorized"))
    assert _should_retry_provider_error(_resp("API key not valid. Please pass a valid API key."))
    assert _should_retry_provider_error(_resp("PERMISSION_DENIED: caller lacks authentication"))


def test_success_and_generic_400_not_retryable():
    assert not _should_retry_provider_error(_resp("", status="success"))
    # a plain schema error without credential/availability tokens stays terminal
    assert not _should_retry_provider_error(_resp("HTTP 400: messages field is required"))


def test_invalid_key_demotes_provider_runtime():
    assert _provider_runtime_failure_reason(_resp(GOOGLE_400)) == "runtime_auth_failed:invalid_api_key"
    assert _provider_runtime_failure_reason(_resp("everything is fine", status="success")) == ""
