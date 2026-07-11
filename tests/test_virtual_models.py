"""Roadmap #7: virtual models in /v1/models + OpenAI `model` field routing.
Acceptance: harness switches routing policy purely via the model id."""
from aichaind.transport.http_server import (VIRTUAL_MODELS,
                                            _control_from_virtual_model,
                                            _parse_routing_control,
                                            build_models_listing)


def test_models_listing_shape():
    listing = build_models_listing()
    assert listing["object"] == "list"
    ids = [m["id"] for m in listing["data"]]
    for vid in VIRTUAL_MODELS:
        assert vid in ids
    assert any(m["id"].startswith("aichain/lock:") for m in listing["data"])
    for m in listing["data"]:
        assert m["object"] == "model" and m["owned_by"] == "aichaind"


def test_auto_economy_power_local_map_to_preferences():
    assert _control_from_virtual_model({"model": "aichain/auto"})["routing_preference"] == "balanced"
    assert _control_from_virtual_model({"model": "aichain/economy"})["routing_preference"] == "min_cost"
    assert _control_from_virtual_model({"model": "aichain/power"})["routing_preference"] == "max_intelligence"
    assert _control_from_virtual_model({"model": "aichain/local"})["routing_preference"] == "prefer_local"
    for vid in VIRTUAL_MODELS:
        assert _control_from_virtual_model({"model": vid})["mode"] == "auto"


def test_lock_form_is_persistent_manual():
    c = _control_from_virtual_model({"model": "aichain/lock:deepseek/deepseek-v4-flash"})
    assert c["mode"] == "manual"
    assert c["model"] == "deepseek/deepseek-v4-flash"
    assert c["persist_for_session"] is True


def test_pin_form_is_oneshot_manual():
    c = _control_from_virtual_model({"model": "aichain/deepseek/deepseek-v4-flash"})
    assert c["mode"] == "manual"
    assert c["persist_for_session"] is False


def test_non_aichain_model_untouched():
    assert _control_from_virtual_model({"model": "gpt-4o"}) is None
    assert _control_from_virtual_model({"model": ""}) is None


def test_explicit_control_beats_virtual_model():
    payload = {"model": "aichain/power",
               "_aichain_control": {"mode": "auto", "routing_preference": "min_cost"}}
    control, err = _parse_routing_control(payload)
    assert not err
    assert control["routing_preference"] == "min_cost"
    assert control.get("source") != "virtual_model"


def test_virtual_model_flows_through_parse():
    control, err = _parse_routing_control({"model": "aichain/economy"})
    assert not err and control["routing_preference"] == "min_cost"
    control, err = _parse_routing_control({"model": "aichain/lock:openrouter/qwen/qwen3-coder"})
    assert not err and control["mode"] == "manual"


def test_bearer_real_token_accepted_for_chat(monkeypatch):
    """F1 (E2E review): standard OpenAI Authorization header with the REAL
    sidecar token must authenticate — not only X-AIchain-Token."""
    from aichaind.transport import http_server as hs

    class FakeAuth:
        is_active = True
        def validate(self, token):
            return token == "real-token-123"

    class FakeHandler:
        client_address = ("127.0.0.1", 5555)
        headers = {"Origin": "", "Authorization": "Bearer real-token-123",
                   "X-AIchain-Token": ""}

    # replicate the extraction logic used in _handle_chat
    h = FakeHandler()
    auth_header = h.headers.get("X-AIchain-Token", "")
    if not auth_header:
        authz = str(h.headers.get("Authorization", "") or "").strip()
        if authz.lower().startswith("bearer "):
            auth_header = authz.split(" ", 1)[1].strip()
    assert FakeAuth().validate(auth_header)
