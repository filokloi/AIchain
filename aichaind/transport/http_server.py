#!/usr/bin/env python3
"""
aichaind.transport.http_server — Hardened HTTP Proxy Server

Merged from scripts/aichain_bridge.py and root aichain_bridge.py.
Security hardened:
  - Binds 127.0.0.1 ONLY (never 0.0.0.0)
  - Auth token validation on every request
  - Origin validation
  - Rate limiting
  - PII redaction before cloud routing
  - Policy-gated routing decisions
  - Output validation
"""

import json
import time
import uuid
import hashlib
import logging
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aichaind.security.auth import AuthTokenManager, validate_origin
from aichaind.security.rate_limiter import TokenBucketRateLimiter
from aichaind.security.redactor import PIIRedactor, redact_messages, scan_messages
from aichaind.security.injection_guard import PromptInjectionGuard
from aichaind.core.policy import PolicyEngine, PolicyResult
from aichaind.core.state_machine import Controller
from aichaind.core.session import SessionStore, ProviderRun, PrivacyContext
from aichaind.routing.cascade import CascadeRouter
from aichaind.routing.rules import detect_visual_content
from aichaind.providers.registry import get_adapter, get_adapter_for_model
from aichaind.providers.base import CompletionRequest
from aichaind.telemetry.audit import AuditLogger

log = logging.getLogger("aichaind.transport")

_auth_manager: AuthTokenManager = None
_rate_limiter: TokenBucketRateLimiter = None
_cascade_router: CascadeRouter = None
_audit_logger: AuditLogger = None
_policy_engine: PolicyEngine = None
_controller: Controller = None
_session_store: SessionStore = None
_pii_redactor: PIIRedactor = None
_roles: dict = {}
_version: str = "5.0.0"
_balance_checker = None
_discovery_report = None
_route_eval_collector = None
_summarizer = None
_injection_guard: PromptInjectionGuard = None
_provider_access_layer = None
_local_profile_store = None
_input_redaction_enabled = True

_CLOUD_PROVIDERS = {
    "openrouter", "openai", "openai-codex", "google", "anthropic", "deepseek", "groq",
    "mistral", "xai", "cohere", "moonshot", "zhipu",
}
_LOCAL_PROVIDERS = {"local", "vllm", "ollama", "lmstudio", "llamacpp"}
_DANGEROUS_PATTERNS = re.compile(
    r"(rm\s+-rf\s+/|DROP\s+TABLE|DELETE\s+FROM\s+\*|"
    r"exec\s*\(|eval\s*\(|__import__|os\.system|subprocess\.)",
    re.IGNORECASE,
)
_COMMAND_LINE_RE = re.compile(
    r"(?im)^(?:\$|PS>|python\b|pytest\b|git\b|npm\b|uv\b|pip\b|node\b|curl\b|invoke-restmethod\b|get-content\b).+"
)
_MODEL_ID_RE = re.compile(r"\b[a-z0-9_-]+/[a-z0-9_.:-]+\b", re.IGNORECASE)
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'<>|]+")
_RELATIVE_FILE_RE = re.compile(
    r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|md|json|ya?ml|txt|js|ts|html|css|toml|ini|cfg)\b",
    re.IGNORECASE,
)
_FILE_NAME_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.(?:py|md|json|ya?ml|txt|js|ts|html|css|toml|ini|cfg)\b", re.IGNORECASE)
_SECRET_TOKEN_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|ghp_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,})"
)
_SENSITIVE_FILE_ACCESS_RE = re.compile(
    r"(id_rsa|id_ed25519|\.auth_token|openclaw\.json|\.env|/etc/passwd|/etc/shadow|\.ssh/|Get-Content\s+\$HOME\\\.openclaw|cat\s+~/.ssh)",
    re.IGNORECASE,
)


class AichainDHandler(BaseHTTPRequestHandler):
    """Hardened HTTP proxy handler for the aichaind sidecar."""

    def log_message(self, format, *args):
        log.info(format % args)

    def do_GET(self):
        if self.path == "/health":
            self._handle_health()
        else:
            self.send_error(404, "Not Found")

    def _handle_health(self):
        state = _controller.state if _controller else {}
        health = {
            "status": "ok",
            "version": _version,
            "system_state": str(state.get("system", "UNKNOWN")),
            "circuit_state": str(state.get("circuit", "UNKNOWN")),
            "godmode": bool(state.get("godmode")),
            "fast_brain": _roles.get("fast_brain", ""),
            "heavy_brain": _roles.get("heavy_brain", ""),
            "local_brain": _roles.get("local_brain", ""),
            "auth_active": _auth_manager.is_active if _auth_manager else False,
            "provider_access": _provider_access_summary(),
            "local_profiles": _local_profile_summary(),
        }
        self._send_json(200, health)

    def do_POST(self):
        try:
            if self.path == "/v1/chat/completions":
                self._handle_chat()
            else:
                self.send_error(404, "Not Found")
        except Exception as exc:
            log.exception("Unhandled request error")
            try:
                self._send_json(500, {"error": f"Internal server error: {type(exc).__name__}"})
            except Exception:
                self.close_connection = True

    def _handle_chat(self):
        origin = self.headers.get("Origin", "")
        if not validate_origin(origin):
            self.send_error(403, "Forbidden: Invalid origin")
            if _audit_logger:
                _audit_logger.record_auth_failure(f"origin_rejected: {origin}")
            return

        auth_header = self.headers.get("X-AIchain-Token", "")
        if _auth_manager and _auth_manager.is_active and not _auth_manager.validate(auth_header):
            self.send_error(401, "Unauthorized: Invalid token")
            if _audit_logger:
                _audit_logger.record_auth_failure("invalid_token")
            return

        client_ip = self.client_address[0]
        if _rate_limiter and not _rate_limiter.allow(client_ip):
            self.send_error(429, "Too Many Requests")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Bad Request: Invalid JSON")
            return

        messages = payload.get("messages", [])
        session = _load_or_create_session(payload)
        _update_session_summary_state(session, messages)

        injection_result = _scan_for_prompt_injection(messages)
        if injection_result and injection_result.blocked:
            _record_injection_block(session, injection_result)
            self._send_json(403, {
                "error": injection_result.reason,
                "session_id": session.session_id if session else "",
                "_aichaind": {
                    "injection_risk": injection_result.risk,
                    "injection_matches": injection_result.matches,
                },
            })
            return

        redaction_map = {}
        pii_categories = []
        pii_redacted = False
        if _pii_redactor:
            existing_map = session.redaction_map if session else None
            if _input_redaction_enabled:
                messages, redaction_map, pii_categories = redact_messages(messages, _pii_redactor, existing_map)
                pii_redacted = bool(pii_categories)
                if pii_categories:
                    log.info(f"PII detected & redacted: {pii_categories}")
            else:
                pii_categories = scan_messages(messages, _pii_redactor)
                if pii_categories:
                    log.info(f"PII detected (redaction disabled): {pii_categories}")

        contains_pii = bool(pii_categories)
        if session:
            session.privacy_context.contains_pii = contains_pii
            session.privacy_context.pii_categories = pii_categories
            session.redaction_map.update(redaction_map)

        policy_result = _evaluate_request_policy(session, contains_pii)
        if policy_result and not policy_result.allowed:
            _record_route_eval(
                session=session,
                messages=messages,
                decision=None,
                exec_status="policy_blocked",
                exec_latency_ms=0.0,
                pii_detected=contains_pii,
                godmode=False,
            )
            _record_policy_block(session, policy_result.reason)
            self._send_json(403, {
                "error": f"Policy: {policy_result.reason}",
                "session_id": session.session_id if session else "",
            })
            return

        messages, compression_meta = _maybe_compress_messages(session, messages)

        balance_report = None
        if _balance_checker and _discovery_report:
            try:
                balance_report = _balance_checker.check_all(_discovery_report.credentials)
            except Exception as e:
                log.warning(f"Balance check failed, continuing without optimizer context: {e}")

        godmode_model = None
        if _controller and _controller.is_godmode:
            godmode_model = _controller.state.get("godmode", {}).get("model")

        privacy_ctx = PrivacyContext(
            contains_pii=contains_pii,
            pii_categories=pii_categories,
            cloud_routing_allowed=not (policy_result and policy_result.block_cloud),
        )

        decision = _cascade_router.route(
            messages=messages,
            godmode_model=godmode_model,
            available_free_model=_roles.get("fast_brain", ""),
            available_heavy_model=_roles.get("heavy_brain", ""),
            available_visual_model=_roles.get("visual_brain", ""),
            available_local_model=_roles.get("local_brain", ""),
            privacy_context=privacy_ctx,
            balance_report=balance_report,
            budget_state=session.budget_state if session else None,
        )

        target_model = decision.target_model or _roles.get("fast_brain", "openrouter/google/gemini-2.5-flash:free")
        target_provider = getattr(decision, "target_provider", "") or ""
        decision, target_model, target_provider, local_reroute_used = _maybe_force_local_privacy_route(
            decision=decision,
            initial_policy=policy_result,
            target_model=target_model,
            target_provider=target_provider,
        )
        decision, target_model, target_provider, codex_bridge_used = _maybe_route_openai_codex_oauth(
            decision=decision,
            target_model=target_model,
            target_provider=target_provider,
        )
        decision, target_model, target_provider, access_decision, access_failover_used, access_block_reason = _ensure_provider_access(
            decision=decision,
            payload=payload,
            target_model=target_model,
            target_provider=target_provider,
            balance_report=balance_report,
        )
        if access_block_reason:
            _record_route_eval(
                session=session,
                messages=messages,
                decision=decision,
                exec_status="provider_access_blocked",
                exec_latency_ms=0.0,
                pii_detected=contains_pii,
                godmode=bool(godmode_model),
            )
            if _audit_logger:
                _audit_logger.record("provider_access_blocked", {
                    "provider": target_provider or _infer_provider(target_model),
                    "reason": access_block_reason,
                }, session_id=session.session_id if session else "")
            _save_session(session)
            self._send_json(503, {
                "error": access_block_reason,
                "session_id": session.session_id if session else "",
                "_aichaind": {
                    "routed_model": target_model,
                    "routed_provider": target_provider or _infer_provider(target_model),
                    "route_confidence": decision.confidence,
                    "route_layers": decision.decision_layers,
                    "estimated_cost_usd": round(getattr(decision, "estimated_cost_usd", 0.0), 6),
                    "provider_access": access_decision.to_dict() if access_decision else {},
                },
            })
            return

        final_policy, policy_block_reason = _enforce_final_route_policy(
            session=session,
            initial_policy=policy_result,
            contains_pii=contains_pii,
            target_model=target_model,
            target_provider=target_provider,
            estimated_cost_usd=getattr(decision, "estimated_cost_usd", 0.0),
        )
        if policy_block_reason:
            _record_route_eval(
                session=session,
                messages=messages,
                decision=decision,
                exec_status="policy_blocked",
                exec_latency_ms=0.0,
                pii_detected=contains_pii,
                godmode=bool(godmode_model),
            )
            _record_policy_block(session, policy_block_reason, model=target_model, provider=target_provider)
            self._send_json(403, {
                "error": f"Policy: {policy_block_reason}",
                "session_id": session.session_id if session else "",
                "_aichaind": {
                    "routed_model": target_model,
                    "routed_provider": target_provider or _infer_provider(target_model),
                    "route_confidence": decision.confidence,
                    "route_layers": decision.decision_layers,
                    "estimated_cost_usd": round(getattr(decision, "estimated_cost_usd", 0.0), 6),
                    "provider_access_method": access_decision.selected_method if access_decision else "",
                    "provider_access_status": access_decision.status if access_decision else "",
                },
            })
            return

        target_provider = target_provider or _infer_provider(target_model)
        log.info(
            f"Route: {target_model} via {target_provider or 'auto'} "
            f"(confidence={decision.confidence:.2f}, layers={decision.decision_layers}, pii={contains_pii}, access={access_decision.selected_method if access_decision else ''})"
        )

        if _audit_logger:
            _audit_logger.record_route(
                model=target_model,
                confidence=decision.confidence,
                layers=decision.decision_layers,
                latency_ms=decision.latency_ms,
                session_id=session.session_id if session else "",
            )

        adapter = get_adapter(target_provider) if target_provider else None
        if not adapter:
            adapter = get_adapter_for_model(target_model)
        if not adapter:
            _save_session(session)
            self.send_error(500, "No adapter for model")
            return

        request = _build_request(payload, messages, target_model, adapter)
        start_t = time.time()
        response = adapter.execute(request)
        exec_latency = (time.time() - start_t) * 1000

        decision, target_model, target_provider, adapter, response, exec_latency, failover_used = _attempt_provider_failover(
            decision=decision,
            payload=payload,
            messages=messages,
            balance_report=balance_report,
            target_model=target_model,
            target_provider=target_provider or adapter.name,
            adapter=adapter,
            response=response,
            exec_latency=exec_latency,
        )

        if _controller:
            if response.status == "success":
                _controller.record_success()
            elif response.status in ("error", "timeout"):
                action = _controller.record_error((response.error or "")[:100])
                if action == "ESCALATE":
                    log.warning("Controller requesting escalation")

        _restore_redactions(response, redaction_map)
        if response.status == "success" and response.content:
            _update_session_summary_state(session, [{"role": "assistant", "content": response.content}])

        if response.status == "success" and response.content:
            validation = _validate_output(response.content)
            if not validation["safe"]:
                log.warning(f"Output validation failed: {validation['reason']}")
                _record_route_eval(
                    session=session,
                    messages=messages,
                    decision=decision,
                    exec_status="output_blocked",
                    exec_latency_ms=exec_latency,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    pii_detected=contains_pii,
                    godmode=bool(godmode_model),
                )
                if _audit_logger:
                    _audit_logger.record("output_blocked", {
                        "model": target_model,
                        "reason": validation["reason"],
                    }, session_id=session.session_id if session else "")
                _record_session_run(
                    session=session,
                    model=target_model,
                    provider=target_provider or adapter.name,
                    response=response,
                    exec_latency=exec_latency,
                    estimated_cost_usd=getattr(decision, "estimated_cost_usd", 0.0),
                    status_override="blocked_output",
                    error_text=validation["reason"],
                )
                self._send_json(422, {
                    "error": f"Output blocked: {validation['reason']}",
                    "session_id": session.session_id if session else "",
                    "_aichaind": {
                        "routed_model": target_model,
                        "routed_provider": target_provider or adapter.name,
                        "route_confidence": decision.confidence,
                        "route_layers": decision.decision_layers,
                        "route_latency_ms": round(decision.latency_ms, 2),
                        "exec_latency_ms": round(exec_latency, 2),
                        "pii_detected": contains_pii,
                    "pii_redacted": pii_redacted,
                        "estimated_cost_usd": round(getattr(decision, "estimated_cost_usd", 0.0), 6),
                        "output_blocked": True,
                        "local_reroute_used": local_reroute_used,
                        "codex_oauth_bridge_used": codex_bridge_used,
                        "compression": compression_meta,
                    },
                })
                return

        if response.status == "success":
            result = response.raw_response or {
                "choices": [{"message": {"content": response.content}}],
                "usage": {
                    "prompt_tokens": response.input_tokens,
                    "completion_tokens": response.output_tokens,
                },
            }
            result["_aichaind"] = {
                "session_id": session.session_id if session else "",
                "routed_model": target_model,
                "routed_provider": target_provider or adapter.name,
                "route_confidence": decision.confidence,
                "route_layers": decision.decision_layers,
                "route_latency_ms": round(decision.latency_ms, 2),
                "exec_latency_ms": round(exec_latency, 2),
                "pii_detected": contains_pii,
                    "pii_redacted": pii_redacted,
                "estimated_cost_usd": round(getattr(decision, "estimated_cost_usd", 0.0), 6),
                "cost_tier": getattr(decision, "cost_tier", ""),
                "model_preference": getattr(decision, "model_preference", ""),
                "providers_with_credits": balance_report.providers_with_credits if balance_report else [],
                "failover_used": failover_used,
                "provider_access_failover_used": access_failover_used,
                "provider_access_method": access_decision.selected_method if access_decision else "",
                "provider_access_status": access_decision.status if access_decision else "",
                "fallback_chain": getattr(decision, "fallback_chain", []),
                "local_reroute_used": local_reroute_used,
                "codex_oauth_bridge_used": codex_bridge_used,
                "compression": compression_meta,
            }
            _record_route_eval(
                session=session,
                messages=messages,
                decision=decision,
                exec_status=response.status,
                exec_latency_ms=exec_latency,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                finish_reason=response.finish_reason,
                pii_detected=contains_pii,
                godmode=bool(godmode_model),
            )
            _record_session_run(
                session=session,
                model=target_model,
                provider=target_provider or adapter.name,
                response=response,
                exec_latency=exec_latency,
                estimated_cost_usd=getattr(decision, "estimated_cost_usd", 0.0),
            )
            self._send_json(200, result)
        else:
            log.error(f"Provider error: {response.error}")
            _record_route_eval(
                session=session,
                messages=messages,
                decision=decision,
                exec_status=response.status,
                exec_latency_ms=exec_latency,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                finish_reason=response.finish_reason,
                pii_detected=contains_pii,
                godmode=bool(godmode_model),
            )
            _record_session_run(
                session=session,
                model=target_model,
                provider=target_provider or adapter.name,
                response=response,
                exec_latency=exec_latency,
                estimated_cost_usd=getattr(decision, "estimated_cost_usd", 0.0),
                error_text=response.error,
            )
            self._send_json(502, {
                "error": response.error,
                "session_id": session.session_id if session else "",
            })

    def _send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _load_or_create_session(payload: dict):
    if not _session_store:
        return None

    session_id = str(payload.get("session_id", "") or "").strip()
    session = _session_store.load(session_id) if session_id else None
    if session is None:
        session = _session_store.create()
    session.advance_turn()
    return session


def _provider_access_summary() -> dict:
    if not _provider_access_layer:
        return {}
    summary = {}
    for provider, decision in _provider_access_layer.summary().items():
        summary[provider] = {
            "method": decision.get("selected_method", "disabled"),
            "status": decision.get("status", "disabled"),
            "reason": decision.get("reason", ""),
            "runtime_confirmed": bool(decision.get("runtime_confirmed", False)),
            "target_form_reached": bool(decision.get("target_form_reached", False)),
            "configured_methods": decision.get("configured_methods", []),
            "available_methods": decision.get("available_methods", []),
            "fallback_methods": decision.get("fallback_methods", []),
            "billing_basis": decision.get("billing_basis", ""),
            "usage_tracking": decision.get("usage_tracking", ""),
            "quota_visibility": decision.get("quota_visibility", ""),
            "limitations": decision.get("limitations", []),
            "project_verification": decision.get("project_verification", ""),
        }
    return summary


def _local_profile_summary() -> dict:
    if not _local_profile_store:
        return {}
    try:
        return _local_profile_store.summary(_roles.get('local_brain', ''))
    except Exception as exc:
        return {'error': str(exc)}


def _save_session(session) -> None:
    if not session or not _session_store:
        return
    if _controller:
        session.system_state = str(_controller.state.get("system", session.system_state))
        session.circuit_state = str(_controller.state.get("circuit", session.circuit_state))
    _session_store.save(session)


def _budget_context(session) -> tuple[float, float]:
    if not session:
        return 0.0, 10.0
    return session.budget_state.total_spent_usd, session.budget_state.session_limit_usd


def _evaluate_request_policy(session, contains_pii: bool) -> PolicyResult | None:
    if not _policy_engine:
        return None
    budget_spent, budget_limit = _budget_context(session)
    return _policy_engine.evaluate(
        contains_pii=contains_pii,
        budget_spent=budget_spent,
        budget_limit=budget_limit,
    )


def _merge_policy_results(initial: PolicyResult | None, final: PolicyResult | None) -> PolicyResult | None:
    if not initial:
        return final
    if not final:
        return initial

    merged = PolicyResult(
        allowed=initial.allowed and final.allowed,
        reason="|".join(filter(None, [initial.reason, final.reason])),
        force_model=final.force_model or initial.force_model,
        force_provider=final.force_provider or initial.force_provider,
        block_cloud=initial.block_cloud or final.block_cloud,
        prefer_local=initial.prefer_local or final.prefer_local,
        max_cost_per_turn=0.0,
    )

    positive_limits = [value for value in (initial.max_cost_per_turn, final.max_cost_per_turn) if value > 0]
    if positive_limits:
        merged.max_cost_per_turn = min(positive_limits)
    return merged


def _infer_provider(model_id: str) -> str:
    if not model_id:
        return "unknown"
    return model_id.split("/", 1)[0].lower() if "/" in model_id else model_id.lower()


def _is_cloud_route(target_provider: str, target_model: str) -> bool:
    provider = (target_provider or _infer_provider(target_model)).lower()
    if provider in _LOCAL_PROVIDERS:
        return False
    return provider in _CLOUD_PROVIDERS or provider not in _LOCAL_PROVIDERS


def _enforce_final_route_policy(
    session,
    initial_policy: PolicyResult | None,
    contains_pii: bool,
    target_model: str,
    target_provider: str,
    estimated_cost_usd: float,
) -> tuple[PolicyResult | None, str]:
    if not _policy_engine:
        return initial_policy, ""

    budget_spent, budget_limit = _budget_context(session)
    final_policy = _policy_engine.evaluate(
        target_model=target_model,
        target_provider=target_provider or _infer_provider(target_model),
        contains_pii=contains_pii,
        budget_spent=budget_spent,
        budget_limit=budget_limit,
    )
    effective = _merge_policy_results(initial_policy, final_policy)
    if effective and not effective.allowed:
        return effective, effective.reason or "policy_denied"

    if effective and effective.block_cloud and _is_cloud_route(target_provider, target_model):
        return effective, "cloud_routing_blocked_by_policy"

    if effective and effective.max_cost_per_turn > 0 and estimated_cost_usd > effective.max_cost_per_turn:
        return effective, "per_turn_cost_exceeds_policy"

    return effective, ""


def _record_policy_block(session, reason: str, model: str = "", provider: str = "") -> None:
    if _audit_logger:
        _audit_logger.record(
            "policy_blocked",
            {"reason": reason, "model": model, "provider": provider},
            actor="policy",
            session_id=session.session_id if session else "",
        )
    _save_session(session)


def _scan_for_prompt_injection(messages: list[dict]):
    if not _injection_guard:
        return None
    return _injection_guard.scan_messages(messages)


def _record_injection_block(session, scan_result) -> None:
    if _audit_logger:
        _audit_logger.record(
            "prompt_injection_blocked",
            {
                "risk": scan_result.risk,
                "score": scan_result.score,
                "matches": scan_result.matches,
                "reason": scan_result.reason,
            },
            actor="policy",
            session_id=session.session_id if session else "",
        )
    _save_session(session)


def _merge_unique(existing: list[str], new_values: list[str], limit: int = 25) -> list[str]:
    merged = []
    seen = set()
    for value in (existing or []) + (new_values or []):
        normalized = str(value or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
        if len(merged) >= limit:
            break
    return merged


def _flatten_message_text(messages: list[dict]) -> str:
    parts = []
    for msg in messages or []:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or part.get("input_text") or ""
                    if text:
                        parts.append(text)
    return "\n".join(filter(None, parts))


def _extract_context_artifacts(messages: list[dict]) -> dict:
    text = _flatten_message_text(messages)
    commands = [match.strip()[:220] for match in _COMMAND_LINE_RE.findall(text)]
    file_paths = [match.strip()[:220] for match in _WINDOWS_PATH_RE.findall(text)]
    file_paths.extend(match.strip()[:220] for match in _RELATIVE_FILE_RE.findall(text))
    file_paths.extend(match.strip()[:220] for match in _FILE_NAME_RE.findall(text))
    identifiers = [match.strip()[:160] for match in _MODEL_ID_RE.findall(text)]
    artifacts = _merge_unique([], file_paths + identifiers, limit=40)
    return {
        "commands": _merge_unique([], commands, limit=20),
        "file_paths": _merge_unique([], file_paths, limit=25),
        "identifiers": _merge_unique([], identifiers, limit=40),
        "artifacts": artifacts,
    }


def _extract_active_plan(messages: list[dict]) -> str:
    for msg in reversed(messages or []):
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        plan_lines = [line for line in lines if re.match(r"^(?:\d+\.|[-*])\s+", line)]
        if plan_lines:
            return " | ".join(plan_lines[:5])[:500]
    return ""


def _extract_open_loops(messages: list[dict]) -> list[str]:
    loops = []
    for msg in messages or []:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        for line in content.splitlines():
            normalized = line.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if normalized.endswith("?") or any(token in lowered for token in ("todo", "next step", "follow up", "remaining", "open issue")):
                loops.append(normalized[:220])
    return _merge_unique([], loops, limit=20)


def _update_session_summary_state(session, messages: list[dict]) -> None:
    if not session or not messages:
        return
    extracted = _extract_context_artifacts(messages)
    state = session.summary_state
    state.commands = _merge_unique(state.commands, extracted["commands"], limit=25)
    state.file_paths = _merge_unique(state.file_paths, extracted["file_paths"], limit=30)
    state.identifiers = _merge_unique(state.identifiers, extracted["identifiers"], limit=40)
    state.artifacts = _merge_unique(state.artifacts, extracted["artifacts"], limit=40)
    state.open_loops = _merge_unique(state.open_loops, _extract_open_loops(messages), limit=20)
    active_plan = _extract_active_plan(messages)
    if active_plan:
        state.active_plan = active_plan


def _missing_artifacts(messages: list[dict], artifacts: list[str]) -> list[str]:
    if not artifacts:
        return []
    text = _flatten_message_text(messages).lower()
    return [artifact for artifact in artifacts if artifact and artifact.lower() not in text]


def _inject_pinned_artifacts(messages: list[dict], artifacts: list[str]) -> list[dict]:
    if not artifacts:
        return messages
    pinned = {
        "role": "system",
        "content": "[Pinned Artifacts — preserve exactly]\n" + "\n".join(f"- {item}" for item in artifacts[:10]),
    }
    updated = list(messages)
    insert_at = 1 if updated and updated[0].get("role") == "system" else 0
    updated.insert(insert_at, pinned)
    return updated


def _maybe_force_local_privacy_route(decision, initial_policy: PolicyResult | None, target_model: str, target_provider: str):
    if not initial_policy or not (initial_policy.block_cloud or initial_policy.prefer_local):
        return decision, target_model, target_provider, False
    if not _is_cloud_route(target_provider, target_model):
        return decision, target_model, target_provider, False

    local_model = ((_roles or {}).get("local_brain") or "").strip()
    if not local_model:
        return decision, target_model, target_provider, False

    prior_model = target_model
    local_provider = _infer_provider(local_model)
    decision.target_model = local_model
    decision.target_provider = local_provider
    decision.estimated_cost_usd = 0.0
    decision.cost_tier = "local"
    if prior_model:
        decision.fallback_chain = getattr(decision, "fallback_chain", []) + [prior_model]
    decision.reason = f"{decision.reason}|privacy_local_reroute" if decision.reason else "privacy_local_reroute"
    return decision, local_model, local_provider, True


def _maybe_route_openai_codex_oauth(decision, target_model: str, target_provider: str):
    if not _provider_access_layer:
        return decision, target_model, target_provider, False

    provider = (target_provider or _infer_provider(target_model)).lower()
    normalized_model = (target_model or "").strip().lower()
    if provider not in {"openai", "openai-codex"}:
        return decision, target_model, target_provider, False
    if not normalized_model or ("gpt-5" not in normalized_model and "codex" not in normalized_model):
        return decision, target_model, target_provider, False

    codex_access = _provider_access_layer.resolve("openai-codex")
    if codex_access.selected_method == "disabled" or not codex_access.runtime_confirmed:
        return decision, target_model, target_provider, False

    codex_adapter = get_adapter("openai-codex")
    if not codex_adapter:
        return decision, target_model, target_provider, False

    try:
        discovery = codex_adapter.discover()
        available_models = list(getattr(discovery, "available_models", []) or [])
        resolved_model = codex_adapter.resolve_preferred_model(target_model, available_models)
    except Exception:
        return decision, target_model, target_provider, False

    if not resolved_model:
        return decision, target_model, target_provider, False
    if provider == "openai-codex" and resolved_model == target_model:
        return decision, target_model, target_provider, False

    prior_model = target_model
    decision.target_model = resolved_model
    decision.target_provider = "openai-codex"
    decision.estimated_cost_usd = 0.0
    decision.cost_tier = "oauth_window"
    if prior_model and prior_model != resolved_model:
        decision.fallback_chain = getattr(decision, "fallback_chain", []) + [prior_model]
    decision.reason = (
        f"{decision.reason}|oauth_bridge:openai-codex"
        if decision.reason else "oauth_bridge:openai-codex"
    )
    return decision, resolved_model, "openai-codex", True

def _ensure_provider_access(decision, payload: dict, target_model: str, target_provider: str, balance_report):
    provider = (target_provider or _infer_provider(target_model)).lower()
    if not _provider_access_layer:
        return decision, target_model, provider, None, False, ""

    access_decision = _provider_access_layer.resolve(provider)
    if (
        access_decision.selected_method != "disabled"
        and (
            access_decision.status != "target_form_not_reached"
            or access_decision.runtime_confirmed
        )
    ):
        return decision, target_model, provider, access_decision, False, ""

    cost_optimizer = getattr(_cascade_router, "_cost_optimizer", None)
    if not cost_optimizer or not balance_report:
        return (
            decision,
            target_model,
            provider,
            access_decision,
            False,
            f"provider_access_unavailable:{provider}:{access_decision.reason}",
        )

    available_models = {
        "free": _roles.get("fast_brain", ""),
        "heavy": _roles.get("heavy_brain", ""),
        "visual": _roles.get("visual_brain", ""),
        "local": _roles.get("local_brain", ""),
    }
    excluded_providers = {provider}
    excluded_models = {target_model}
    model_preference = getattr(decision, "model_preference", "free") or "free"

    for _ in range(4):
        alt = cost_optimizer.optimize(
            model_preference=model_preference,
            balance_report=balance_report,
            available_models=available_models,
            estimated_tokens=max(128, payload.get("max_tokens", 512)),
            exclude_providers=excluded_providers,
            exclude_models=excluded_models,
            current_model=target_model,
            current_provider=provider,
        )
        if not alt or not alt.model:
            break

        alt_provider = (alt.provider or _infer_provider(alt.model)).lower()
        alt_access = _provider_access_layer.resolve(alt_provider)
        if alt_access.selected_method == "disabled":
            excluded_providers.add(alt_provider)
            excluded_models.add(alt.model)
            continue

        prior_model = target_model
        decision.target_model = alt.model
        decision.target_provider = alt_provider
        decision.estimated_cost_usd = alt.estimated_cost_usd
        decision.cost_tier = alt.tier
        decision.fallback_chain = getattr(decision, "fallback_chain", []) + [prior_model]
        decision.reason = (
            f"{decision.reason}|access_failover:{provider}:{access_decision.reason}->{alt.reason}"
            if decision.reason else f"access_failover:{provider}:{access_decision.reason}->{alt.reason}"
        )
        return decision, alt.model, alt_provider, alt_access, True, ""

    return (
        decision,
        target_model,
        provider,
        access_decision,
        False,
        f"provider_access_unavailable:{provider}:{access_decision.reason}",
    )


def _record_session_run(
    session,
    model: str,
    provider: str,
    response,
    exec_latency: float,
    estimated_cost_usd: float,
    status_override: str = "",
    error_text: str = "",
) -> None:
    if not session:
        return

    run = ProviderRun(
        run_id=str(uuid.uuid4())[:8],
        model=model,
        provider=provider,
        timestamp=datetime.now(timezone.utc).isoformat(),
        latency_ms=round(exec_latency, 2),
        input_tokens=getattr(response, "input_tokens", 0),
        output_tokens=getattr(response, "output_tokens", 0),
        cost_usd=max(0.0, estimated_cost_usd or 0.0),
        status=status_override or getattr(response, "status", "success"),
        error_text=error_text or getattr(response, "error", ""),
    )
    session.record_run(run)
    _save_session(session)


def _restore_redactions(response, redaction_map: dict) -> None:
    if not redaction_map or not _pii_redactor or not getattr(response, "content", ""):
        return

    response.content = _pii_redactor.de_redact(response.content, redaction_map)
    raw = getattr(response, "raw_response", None)
    if not isinstance(raw, dict):
        return
    for choice in raw.get("choices", []):
        message = choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            message["content"] = _pii_redactor.de_redact(message["content"], redaction_map)


def _maybe_compress_messages(session, messages: list[dict]) -> tuple[list[dict], dict]:
    if session:
        _update_session_summary_state(session, messages)

    if not _summarizer or not messages:
        return messages, {"compressed": False, "chars_saved": 0, "summary_used": False, "verification_passed": True, "artifact_guardrail_used": False}
    if not _summarizer.needs_compression(messages):
        return messages, {"compressed": False, "chars_saved": 0, "summary_used": False, "verification_passed": True, "artifact_guardrail_used": False}

    pinned_facts = session.summary_state.pinned_facts if session else []
    result = _summarizer.compress(messages, pinned_facts=pinned_facts)

    critical_artifacts = []
    if session:
        critical_artifacts = _merge_unique([], (session.summary_state.file_paths or [])[:6] + (session.summary_state.commands or [])[:6], limit=10)

    missing_artifacts = _missing_artifacts(result.compressed_messages, critical_artifacts)
    artifact_guardrail_used = False
    if missing_artifacts:
        artifact_guardrail_used = True
        result.compressed_messages = _inject_pinned_artifacts(result.compressed_messages, missing_artifacts)
        missing_artifacts = _missing_artifacts(result.compressed_messages, critical_artifacts)

    if session:
        session.summary_state.rolling_summary = result.summary_text
        session.summary_state.pinned_facts = list(dict.fromkeys((session.summary_state.pinned_facts or []) + result.pinned_facts + critical_artifacts))

    return result.compressed_messages, {
        "compressed": True,
        "chars_saved": result.chars_saved,
        "summary_used": bool(result.summary_text),
        "compressed_turn_count": result.compressed_turn_count,
        "original_turn_count": result.original_turn_count,
        "verification_passed": not missing_artifacts,
        "artifact_guardrail_used": artifact_guardrail_used,
        "missing_artifacts": missing_artifacts[:10],
    }


def _query_stats(messages: list[dict]) -> tuple[str, int, int]:
    parts = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    parts.append(part.get("text", ""))
    text = "\n".join(filter(None, parts))
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text), len(text.split())


def _record_route_eval(
    session,
    messages: list[dict],
    decision,
    exec_status: str,
    exec_latency_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    finish_reason: str = "",
    pii_detected: bool = False,
    godmode: bool = False,
) -> None:
    if not _route_eval_collector or not decision:
        return

    query_hash, query_length, query_word_count = _query_stats(messages)
    _route_eval_collector.record_from_route(
        query_hash=query_hash,
        query_length=query_length,
        query_word_count=query_word_count,
        decision=decision,
        exec_status=exec_status,
        exec_latency_ms=exec_latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        finish_reason=finish_reason,
        pii_detected=pii_detected,
        godmode=godmode,
        visual_input=detect_visual_content(messages),
    )
    if session:
        ref = f"route_eval:{query_hash[:16]}:{int(time.time())}"
        if ref not in session.telemetry_refs:
            session.telemetry_refs.append(ref)
        cache_ref = f"query:{query_hash[:16]}"
        if cache_ref not in session.cache_refs:
            session.cache_refs.append(cache_ref)


def _build_request(payload: dict, messages: list[dict], target_model: str, adapter) -> CompletionRequest:
    extra = {}
    provider_name = str(getattr(adapter, "name", "") or "").lower()
    if provider_name in _LOCAL_PROVIDERS and _local_profile_store:
        try:
            profile = _local_profile_store.get(target_model)
            if not profile:
                normalized_model = f"{provider_name}/{adapter.format_model_id(target_model)}"
                profile = _local_profile_store.get(normalized_model)
            if isinstance(profile, dict):
                timeout_ms = profile.get("safe_timeout_ms")
                if timeout_ms:
                    extra["timeout_ms"] = int(timeout_ms)
        except Exception:
            pass

    return CompletionRequest(
        model=adapter.format_model_id(target_model),
        messages=messages,
        max_tokens=payload.get("max_tokens", 4096),
        temperature=payload.get("temperature", 0.7),
        stream=payload.get("stream", False),
        extra=extra,
    )


def _should_retry_provider_error(response) -> bool:
    if response.status not in ("error", "timeout"):
        return False
    error_text = (response.error or "").lower()
    retryable_tokens = (
        "429", "quota", "rate limit", "rate_limit", "too many requests",
        "must be verified", "unauthorized", "forbidden", "insufficient",
        "billing", "credits", "not found", "unavailable", "timeout", "timed out",
    )
    return any(token in error_text for token in retryable_tokens)


def _attempt_provider_failover(
    decision,
    payload: dict,
    messages: list[dict],
    balance_report,
    target_model: str,
    target_provider: str,
    adapter,
    response,
    exec_latency: float,
):
    cost_optimizer = getattr(_cascade_router, "_cost_optimizer", None)
    if not cost_optimizer or not balance_report or not _should_retry_provider_error(response):
        return decision, target_model, target_provider, adapter, response, exec_latency, False

    available_models = {
        "free": _roles.get("fast_brain", ""),
        "heavy": _roles.get("heavy_brain", ""),
        "visual": _roles.get("visual_brain", ""),
        "local": _roles.get("local_brain", ""),
    }
    model_preference = getattr(decision, "model_preference", "free") or "free"
    tried_providers = {target_provider or adapter.name}
    tried_models = {target_model}

    for _ in range(3):
        alt = cost_optimizer.optimize(
            model_preference=model_preference,
            balance_report=balance_report,
            available_models=available_models,
            estimated_tokens=max(128, payload.get("max_tokens", 512)),
            exclude_providers=tried_providers,
            exclude_models=tried_models,
        )
        if not alt or not alt.model:
            break

        alt_provider = alt.provider or alt.model.split("/", 1)[0]
        if alt_provider in tried_providers and alt.model in tried_models:
            break

        alt_adapter = get_adapter(alt_provider) or get_adapter_for_model(alt.model)
        if not alt_adapter:
            tried_providers.add(alt_provider)
            tried_models.add(alt.model)
            continue

        log.warning(
            f"Retrying provider failure via fallback: {target_provider}/{target_model} -> {alt_provider}/{alt.model}"
        )
        alt_request = _build_request(payload, messages, alt.model, alt_adapter)
        retry_start = time.time()
        alt_response = alt_adapter.execute(alt_request)
        exec_latency += (time.time() - retry_start) * 1000

        if alt_response.status == "success":
            prior_model = target_model
            decision.target_model = alt.model
            decision.target_provider = alt_provider
            decision.estimated_cost_usd = alt.estimated_cost_usd
            decision.cost_tier = alt.tier
            decision.fallback_chain = getattr(decision, "fallback_chain", []) + [prior_model]
            decision.reason = f"{decision.reason}|failover:{alt.reason}" if decision.reason else f"failover:{alt.reason}"
            return decision, alt.model, alt_provider, alt_adapter, alt_response, exec_latency, True

        tried_providers.add(alt_provider)
        tried_models.add(alt.model)
        target_model = alt.model
        target_provider = alt_provider
        adapter = alt_adapter
        response = alt_response

    return decision, target_model, target_provider, adapter, response, exec_latency, False


class AichainThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ─────────────────────────────────────────
# OUTPUT VALIDATION
# ─────────────────────────────────────────


def _validate_output(content: str) -> dict:
    if _DANGEROUS_PATTERNS.search(content):
        return {"safe": False, "reason": "dangerous_pattern_detected"}
    if _SECRET_TOKEN_RE.search(content):
        return {"safe": False, "reason": "secret_like_token_detected"}
    if _SENSITIVE_FILE_ACCESS_RE.search(content):
        return {"safe": False, "reason": "sensitive_file_access_detected"}
    return {"safe": True, "reason": ""}


# ─────────────────────────────────────────
# SERVER START
# ─────────────────────────────────────────

def start_server(
    port: int = 8080,
    auth_manager: AuthTokenManager = None,
    rate_limiter: TokenBucketRateLimiter = None,
    cascade_router: CascadeRouter = None,
    audit_logger: AuditLogger = None,
    policy_engine: PolicyEngine = None,
    controller: Controller = None,
    session_store: SessionStore = None,
    pii_redactor: PIIRedactor = None,
    injection_guard: PromptInjectionGuard = None,
    roles: dict = None,
    version: str = "5.0.0",
    balance_checker=None,
    discovery_report=None,
    route_eval_collector=None,
    summarizer=None,
    provider_access_layer=None,
    local_profile_store=None,
    input_redaction_enabled: bool = True,
):
    global _auth_manager, _rate_limiter, _cascade_router, _audit_logger
    global _policy_engine, _controller, _session_store, _pii_redactor
    global _roles, _version, _balance_checker, _discovery_report
    global _route_eval_collector, _summarizer, _injection_guard, _provider_access_layer, _local_profile_store, _input_redaction_enabled

    _auth_manager = auth_manager
    _rate_limiter = rate_limiter or TokenBucketRateLimiter()
    _cascade_router = cascade_router or CascadeRouter()
    _audit_logger = audit_logger
    _policy_engine = policy_engine
    _controller = controller
    _session_store = session_store
    _pii_redactor = pii_redactor or PIIRedactor()
    _roles = roles or {}
    _version = version
    _balance_checker = balance_checker
    _discovery_report = discovery_report
    _route_eval_collector = route_eval_collector
    _summarizer = summarizer
    _injection_guard = injection_guard
    _provider_access_layer = provider_access_layer
    _local_profile_store = local_profile_store
    _input_redaction_enabled = bool(input_redaction_enabled)

    server_address = ("127.0.0.1", port)
    httpd = AichainThreadingHTTPServer(server_address, AichainDHandler)

    log.info("=" * 60)
    log.info(f"aichaind v{version} listening on 127.0.0.1:{port}")
    log.info(f"Auth:       {'ACTIVE' if auth_manager and auth_manager.is_active else 'DISABLED'}")
    log.info(f"Rate limit: {_rate_limiter.rate_per_second * 60:.0f} req/min")
    log.info("PII redact: ACTIVE")
    log.info(f"Policy:     {'ACTIVE' if policy_engine else 'DISABLED'}")
    log.info("Output val: ACTIVE")
    log.info(f"Fast Brain:  {_roles.get('fast_brain', 'N/A')}")
    log.info(f"Heavy Brain: {_roles.get('heavy_brain', 'N/A')}")
    log.info(f"Local Brain: {_roles.get('local_brain', 'N/A')}")
    log.info("=" * 60)

    return httpd











