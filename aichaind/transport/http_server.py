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
from urllib.parse import parse_qs, urlparse

from aichaind.security.auth import AuthTokenManager, validate_origin
from aichaind.security.rate_limiter import TokenBucketRateLimiter
from aichaind.security.redactor import PIIRedactor, redact_messages, scan_messages
from aichaind.security.injection_guard import PromptInjectionGuard
from aichaind.core.policy import PolicyEngine, PolicyResult
from aichaind.core.state_machine import Controller
from aichaind.core.session import SessionStore, ProviderRun, PrivacyContext
from aichaind.routing.cascade import CascadeRouter
from aichaind.routing.control_intent import parse_semantic_control
from aichaind.routing.rules import RouteDecision, detect_visual_content, detect_coding_intent, estimate_complexity
from aichaind.providers.registry import get_adapter, get_adapter_for_model
from aichaind.providers.base import CompletionRequest, CompletionResponse
from aichaind.telemetry.audit import AuditLogger
from aichaind.ui.companion_panel import build_companion_panel_html
from aichaind.ui.openclaw_bridge import build_openclaw_bridge_script

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
_routing_preferences = {}

_CLOUD_PROVIDERS = {
    "openrouter", "openai", "openai-codex", "google", "anthropic", "deepseek", "groq",
    "mistral", "xai", "cohere", "moonshot", "zhipu",
}
_LOCAL_PROVIDERS = {"local", "vllm", "ollama", "lmstudio", "llamacpp"}
DEFAULT_OPENCLAW_SESSION_ID = "openclaw-default"
_OPENCLAW_UI_ORIGINS = frozenset({
    "http://127.0.0.1:18789",
    "http://localhost:18789",
    "https://127.0.0.1:18789",
    "https://localhost:18789",
    "http://127.0.0.1:18791",
    "http://localhost:18791",
    "https://127.0.0.1:18791",
    "https://localhost:18791",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
    "https://127.0.0.1:8080",
    "https://localhost:8080",
})
_DANGEROUS_PATTERNS = re.compile(
    r"(rm\s+-rf\s+/|DROP\s+TABLE|DELETE\s+FROM\s+\*)",
    re.IGNORECASE,
)
_CODE_SENSITIVE_PATTERNS = re.compile(
    r"(exec\s*\(|eval\s*\(|__import__|os\.system|subprocess\.)",
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
_STRUCTURED_PROMPT_RE = re.compile(r"\b(json|schema|minified json|structured|yaml|xml|csv|extract|return only)\b", re.IGNORECASE)
_REASONING_PROMPT_RE = re.compile(r"\b(why|how|analy[sz]e|compare|trade-?off|algorithm|proof|theorem|research|security|reason(?:ing)?)\b", re.IGNORECASE)
_CREATIVE_PROMPT_RE = re.compile(r"\b(story|poem|creative|essay|fiction|lyrics)\b", re.IGNORECASE)
_LOCAL_RUNTIME_HEALTH_CACHE: dict[str, tuple[float, bool, str]] = {}
_LOCAL_RUNTIME_HEALTH_TTL_SECONDS = 8.0


class AichainDHandler(BaseHTTPRequestHandler):
    """Hardened HTTP proxy handler for the aichaind sidecar."""

    def log_message(self, format, *args):
        log.info(format % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._handle_health()
        elif parsed.path == "/status":
            self._handle_status()
        elif parsed.path == "/ui/control-state":
            self._handle_ui_control_state(parsed)
        elif parsed.path == "/ui/openclaw-bridge.js":
            self._handle_ui_openclaw_bridge()
        elif parsed.path == "/ui/panel":
            self._handle_ui_panel()
        else:
            self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/ui/"):
            self.send_error(404, "Not Found")
            return
        headers = _ui_cors_headers(self.headers.get("Origin", ""))
        if not headers:
            self.send_error(403, "Forbidden: Invalid UI origin")
            return
        self.send_response(204)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

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
            "routing_preferences": _routing_preferences_summary(),
        }
        self._send_json(200, health)

    def _handle_status(self):
        """Rich operational visibility endpoint."""
        state = _controller.state if _controller else {}
        
        # Catalog age
        catalog_age_seconds = 0.0
        if _discovery_report and hasattr(_discovery_report, "timestamp"):
            catalog_age_seconds = time.time() - getattr(_discovery_report, "timestamp", time.time())
            
        # Provider health (circuit breakers)
        provider_health = {}
        from aichaind.providers.registry import list_providers, get_adapter
        for p in list_providers():
            adapter = get_adapter(p)
            if adapter and hasattr(adapter, "circuit_breaker"):
                provider_health[p] = {
                    "state": adapter.circuit_breaker.state,
                    "is_available": adapter.circuit_breaker.is_available,
                    "failures": getattr(adapter.circuit_breaker, "_failures", 0),
                }

        status_data = {
            "status": "ok",
            "version": _version,
            "uptime_seconds": time.time() - getattr(self, "_server_start_time", time.time()), # Will be approx if not set
            "system_state": str(state.get("system", "UNKNOWN")),
            "routing_mode": "godmode" if state.get("godmode") else "cascade",
            "catalog_age_seconds": round(catalog_age_seconds, 2),
            "provider_health": provider_health,
            "roles": _roles.copy(),
            "auth_active": _auth_manager.is_active if _auth_manager else False,
            "provider_access": _provider_access_summary(),
        }
        self._send_json(200, status_data)

    def _handle_ui_openclaw_bridge(self):
        token = getattr(_auth_manager, "_current_token", "") if _auth_manager else ""
        script = build_openclaw_bridge_script(
            api_base="http://127.0.0.1:8080/ui",
            token=token,
            default_session_id=DEFAULT_OPENCLAW_SESSION_ID,
            panel_base="http://127.0.0.1:8080/ui/panel",
        )
        self._send_text(200, script, "application/javascript; charset=utf-8", {
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        })

    def _handle_ui_panel(self):
        token = getattr(_auth_manager, "_current_token", "") if _auth_manager else ""
        html = build_companion_panel_html(
            api_base="http://127.0.0.1:8080/ui",
            token=token,
            default_session_id=DEFAULT_OPENCLAW_SESSION_ID,
        )
        self._send_text(200, html, "text/html; charset=utf-8", {
            "Cache-Control": "no-store",
        })

    def _validate_ui_request(self, origin: str) -> tuple[bool, dict]:
        headers = _ui_cors_headers(origin)
        if origin and not headers:
            self.send_error(403, "Forbidden: Invalid UI origin")
            return False, {}

        if _is_trusted_ui_origin(origin) or _is_trusted_ui_referer(self.headers.get("Referer", "")):
            return True, headers

        auth_header = self.headers.get("X-AIchain-Token", "")
        if _auth_manager and _auth_manager.is_active and not _auth_manager.validate(auth_header):
            self.send_error(401, "Unauthorized: Invalid token")
            return False, headers
        return True, headers

    def _handle_ui_control_state(self, parsed):
        ok, headers = self._validate_ui_request(self.headers.get("Origin", ""))
        if not ok:
            return
        query = parse_qs(parsed.query or "")
        session_id = str((query.get("session_id", [DEFAULT_OPENCLAW_SESSION_ID])[0]) or "").strip()
        session = _get_ui_session(session_id)
        self._send_json(200, _build_ui_control_state(session), headers)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/v1/chat/completions":
                self._handle_chat()
            elif parsed.path == "/ui/control":
                self._handle_ui_control()
            else:
                self.send_error(404, "Not Found")
        except Exception as exc:
            log.exception("Unhandled request error")
            try:
                self._send_json(500, {"error": f"Internal server error: {type(exc).__name__}"})
            except Exception:
                self.close_connection = True

    def _handle_ui_control(self):
        ok, headers = self._validate_ui_request(self.headers.get("Origin", ""))
        if not ok:
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "Bad Request: Invalid JSON")
            return

        session = _get_ui_session(str(payload.get("session_id", "") or "").strip())
        control_payload = {
            "messages": [],
            "_aichain_control": {
                "mode": str(payload.get("mode", "") or "").strip(),
                "model": str(payload.get("model", "") or "").strip(),
                "provider": str(payload.get("provider", "") or "").strip(),
                "routing_preference": str(payload.get("routing_preference", "balanced") or "balanced").strip(),
                "persist_for_session": bool(payload.get("persist_for_session", True)),
            },
        }
        control, error, changed = _resolve_routing_control(session, control_payload)
        if error:
            self._send_json(400, {"error": error}, headers)
            return
        if changed:
            _save_session(session)
        data = _build_ui_control_state(session, last_confirmation=control.get("control_confirmation", ""))
        self._send_json(200, data, headers)

    def _handle_chat(self):
        origin = self.headers.get("Origin", "")
        if not validate_origin(origin):
            self.send_error(403, "Forbidden: Invalid origin")
            if _audit_logger:
                _audit_logger.record_auth_failure(f"origin_rejected: {origin}")
            return

        auth_header = self.headers.get("X-AIchain-Token", "")
        trusted_openclaw_provider_bridge = _is_trusted_openclaw_provider_bridge(self)
        if (
            _auth_manager
            and _auth_manager.is_active
            and not trusted_openclaw_provider_bridge
            and not _auth_manager.validate(auth_header)
        ):
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
        routing_control, routing_control_error, routing_control_persisted = _resolve_routing_control(session, payload)
        if routing_control_error:
            _save_session(session)
            self._send_json(400, {
                "error": routing_control_error,
                "session_id": session.session_id if session else "",
            })
            return

        messages = routing_control.get("sanitized_messages", messages)
        _update_session_summary_state(session, messages)
        if routing_control_persisted:
            _save_session(session)
        routing_control_meta = _routing_control_response_meta(routing_control)
        if routing_control.get("control_only"):
            _save_session(session)
            self._send_json(200, _build_control_only_response(session, routing_control_meta))
            return

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

        local_reroute_used = False
        if routing_control.get("manual_override_active"):
            decision, target_model, target_provider = _build_manual_route_decision(routing_control)
        else:
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
                routing_preference=routing_control.get("routing_preference", "balanced"),
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
            allow_failover=not routing_control.get("manual_override_active", False),
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
                    **routing_control_meta,
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
                    **routing_control_meta,
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
            allow_failover=not routing_control.get("manual_override_active", False),
        )
        access_decision = _refresh_access_decision_for_provider(target_provider, access_decision)

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
            validation_hint = " | ".join(
                part for part in (
                    getattr(decision, "reason", ""),
                    _payload_prompt_text(payload),
                ) if part
            )
            validation = _validate_output(response.content, task_hint=validation_hint)
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
                        **routing_control_meta,
                    },
                })
                return

        if response.status == "success":
            requested_model = str(payload.get("model", "") or "").strip()
            result = _build_success_response_payload(
                requested_model=requested_model,
                target_model=target_model,
                session=session,
                response=response,
                decision=decision,
                target_provider=target_provider or adapter.name,
                exec_latency=exec_latency,
                contains_pii=contains_pii,
                pii_redacted=pii_redacted,
                balance_report=balance_report,
                failover_used=failover_used,
                access_failover_used=access_failover_used,
                access_decision=access_decision,
                local_reroute_used=local_reroute_used,
                codex_bridge_used=codex_bridge_used,
                compression_meta=compression_meta,
                routing_control_meta=routing_control_meta,
                compat_openclaw_bridge=trusted_openclaw_provider_bridge,
            )
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
            if bool(payload.get("stream", False)):
                self._send_openai_stream(200, result)
            else:
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
                "_aichaind": {
                    **routing_control_meta,
                    "routed_model": target_model,
                    "routed_provider": target_provider or adapter.name,
                },
            })

    def _send_json(self, status_code: int, data: dict, extra_headers: dict | None = None):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, status_code: int, data: str, content_type: str, extra_headers: dict | None = None):
        payload = str(data or "").encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_openai_stream(self, status_code: int, payload: dict):
        frames = _build_openai_stream_frames(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for frame in frames:
            try:
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                break
        self.close_connection = True


def _load_or_create_session(payload: dict):
    if not _session_store:
        return None

    session_id = str(payload.get("session_id", "") or "").strip()
    session = _session_store.load(session_id) if session_id else None
    if session is None:
        session = _session_store.create(session_id=session_id)
    session.advance_turn()
    return session


def _normalize_routing_preference(value: str) -> str:
    normalized = str(value or "balanced").strip().lower()
    if normalized in {"max_intelligence", "min_cost", "prefer_local", "balanced"}:
        return normalized
    return "balanced"


def _parse_routing_control(payload: dict) -> tuple[dict, str]:
    raw = payload.get("_aichain_control")
    if raw is None:
        return {}, ""
    if not isinstance(raw, dict):
        return {}, "invalid_routing_control"

    mode = str(raw.get("mode", "") or "").strip().lower()
    model = str(raw.get("model", "") or raw.get("locked_model", "") or "").strip()
    provider = str(raw.get("provider", "") or raw.get("locked_provider", "") or "").strip().lower()
    persist_for_session = bool(raw.get("persist_for_session", False))
    routing_preference = _normalize_routing_preference(raw.get("routing_preference", "balanced"))

    if mode and mode not in {"auto", "manual"}:
        return {}, "invalid_routing_mode"
    if not mode and (model or provider):
        mode = "manual"

    return {
        "mode": mode,
        "model": model,
        "provider": provider,
        "persist_for_session": persist_for_session,
        "routing_preference": routing_preference,
        "source": "explicit",
        "control_only": False,
        "stripped_prompt": "",
        "confirmation": "",
    }, ""


def _parse_semantic_routing_control(payload: dict) -> dict:
    intent = parse_semantic_control(
        payload.get("messages", []),
        roles=_roles,
        provider_access_summary=_provider_access_layer.summary() if _provider_access_layer else {},
    )
    if not intent:
        return {}
    return {
        "mode": intent.mode,
        "model": intent.model,
        "provider": intent.provider,
        "persist_for_session": intent.persist_for_session,
        "routing_preference": _normalize_routing_preference(intent.routing_preference),
        "source": intent.source,
        "control_only": bool(intent.control_only),
        "stripped_prompt": intent.stripped_prompt,
        "confirmation": intent.confirmation,
    }


def _replace_last_user_message(messages: list[dict], stripped_prompt: str) -> list[dict]:
    if not stripped_prompt:
        return messages
    updated = []
    replaced = False
    for index in range(len(messages) - 1, -1, -1):
        msg = dict(messages[index])
        if not replaced and msg.get("role") == "user":
            msg["content"] = stripped_prompt
            replaced = True
        updated.append(msg)
    updated.reverse()
    return updated


def _resolve_routing_control(session, payload: dict) -> tuple[dict, str, bool]:
    parsed, error = _parse_routing_control(payload)
    if error:
        return {}, error, False
    if not parsed:
        parsed = _parse_semantic_routing_control(payload)

    session_mode = str(getattr(session, "routing_mode", "auto") or "auto") if session else "auto"
    session_model = str(getattr(session, "locked_model", "") or "") if session else ""
    session_provider = str(getattr(session, "locked_provider", "") or "").strip().lower() if session else ""
    session_preference = _normalize_routing_preference(getattr(session, "routing_preference", "balanced") if session else "balanced")

    effective_mode = session_mode if session_mode in {"auto", "manual"} else "auto"
    locked_model = session_model
    locked_provider = session_provider
    routing_preference = session_preference
    changed = False
    control_source = "session"
    control_confirmation = ""
    control_only = False
    sanitized_messages = payload.get("messages", [])

    if effective_mode == "manual" and locked_model and not _manual_lock_target_is_valid(locked_model, locked_provider):
        effective_mode = "auto"
        locked_model = ""
        locked_provider = ""
        if session:
            changed = (
                session.routing_mode != "auto"
                or bool(session.locked_model)
                or bool(session.locked_provider)
            )
            session.routing_mode = "auto"
            session.locked_model = ""
            session.locked_provider = ""

    if parsed:
        control_source = parsed.get("source", "explicit") or "explicit"
        control_confirmation = parsed.get("confirmation", "")
        control_only = bool(parsed.get("control_only", False))
        if parsed.get("stripped_prompt"):
            sanitized_messages = _replace_last_user_message(payload.get("messages", []), parsed["stripped_prompt"])

        requested_mode = parsed.get("mode", "")
        requested_preference = _normalize_routing_preference(parsed.get("routing_preference", session_preference))
        persist = bool(parsed.get("persist_for_session", False))
        routing_preference = requested_preference or session_preference

        if requested_mode == "auto":
            effective_mode = "auto"
            locked_model = ""
            locked_provider = ""
            if persist and session:
                changed = (
                    session.routing_mode != "auto"
                    or bool(session.locked_model)
                    or bool(session.locked_provider)
                    or session.routing_preference != routing_preference
                )
                session.routing_mode = "auto"
                session.locked_model = ""
                session.locked_provider = ""
                session.routing_preference = routing_preference
        elif requested_mode == "manual":
            effective_mode = "manual"
            locked_model = parsed.get("model", "") or session_model
            locked_provider = parsed.get("provider", "") or session_provider or _infer_provider(locked_model)
            if not locked_model:
                return {}, "manual_mode_requires_model", changed
            if not _manual_lock_target_is_valid(locked_model, locked_provider):
                return {}, "invalid_manual_lock_target", changed
            if persist and session:
                changed = (
                    session.routing_mode != "manual"
                    or session.locked_model != locked_model
                    or session.locked_provider != locked_provider
                    or session.routing_preference != routing_preference
                )
                session.routing_mode = "manual"
                session.locked_model = locked_model
                session.locked_provider = locked_provider
                session.routing_preference = routing_preference
        elif persist and session and session.routing_preference != routing_preference:
            changed = True
            session.routing_preference = routing_preference

    if effective_mode != "manual" or not locked_model:
        effective_mode = "auto"
        locked_model = ""
        locked_provider = ""

    return {
        "mode": effective_mode,
        "manual_override_active": effective_mode == "manual" and bool(locked_model),
        "locked_model": locked_model,
        "locked_provider": locked_provider,
        "routing_preference": routing_preference,
        "control_source": control_source,
        "control_changed": changed,
        "control_confirmation": control_confirmation,
        "control_only": control_only,
        "sanitized_messages": sanitized_messages,
    }, "", changed


def _build_manual_route_decision(control: dict):
    locked_model = str(control.get("locked_model", "") or "").strip()
    locked_provider = str(control.get("locked_provider", "") or _infer_provider(locked_model)).strip().lower()
    decision = RouteDecision(
        target_model=locked_model,
        target_provider=locked_provider,
        confidence=1.0,
        decision_layers=["manual_override"],
        reason="manual_override",
    )
    decision.estimated_cost_usd = 0.0
    decision.cost_tier = "manual"
    decision.model_preference = "manual"
    return decision, locked_model, locked_provider


def _manual_lock_target_is_valid(model_id: str, provider: str = "") -> bool:
    normalized_model = str(model_id or "").strip()
    normalized_provider = str(provider or _infer_provider(normalized_model)).strip().lower()
    if not normalized_model or "/" not in normalized_model:
        return False
    if "\\" in normalized_model:
        return False
    if normalized_provider in {"users", "windows", "program files", "program"}:
        return False
    provider_access = _provider_access_summary()
    if normalized_provider in provider_access:
        return True
    if normalized_model in {
        str(_roles.get("fast_brain", "") or "").strip(),
        str(_roles.get("heavy_brain", "") or "").strip(),
        str(_roles.get("visual_brain", "") or "").strip(),
        str(_roles.get("local_brain", "") or "").strip(),
    }:
        return True
    return normalized_provider in _CLOUD_PROVIDERS or normalized_provider in _LOCAL_PROVIDERS


def _routing_control_response_meta(control: dict) -> dict:
    control = control or {}
    return {
        "routing_mode": control.get("mode", "auto"),
        "routing_preference": _normalize_routing_preference(control.get("routing_preference", "balanced")),
        "manual_override_active": bool(control.get("manual_override_active", False)),
        "manual_locked_model": control.get("locked_model", "") if control.get("manual_override_active") else "",
        "manual_locked_provider": control.get("locked_provider", "") if control.get("manual_override_active") else "",
        "control_source": control.get("control_source", "session"),
        "control_changed": bool(control.get("control_changed", False)),
        "control_confirmation": control.get("control_confirmation", ""),
    }


def _build_control_only_response(session, routing_control_meta: dict) -> dict:
    confirmation = routing_control_meta.get("control_confirmation", "AIchain control updated.")
    return {
        "choices": [{"message": {"role": "assistant", "content": confirmation}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "session_id": session.session_id if session else "",
        "_aichaind": {
            "session_id": session.session_id if session else "",
            "control_only": True,
            **routing_control_meta,
        },
    }


def _build_success_response_payload(
    requested_model: str,
    target_model: str,
    session,
    response: CompletionResponse,
    decision: RouteDecision,
    target_provider: str,
    exec_latency: float,
    contains_pii: bool,
    pii_redacted: bool,
    balance_report,
    failover_used: bool,
    access_failover_used: bool,
    access_decision,
    local_reroute_used: bool,
    codex_bridge_used: bool,
    compression_meta: dict,
    routing_control_meta: dict,
    compat_openclaw_bridge: bool = False,
) -> dict:
    model_label = str(requested_model or target_model or response.model or "").strip()
    payload = {
        "id": (
            response.raw_response.get("id", "")
            if isinstance(response.raw_response, dict)
            else ""
        ) or f"chatcmpl_{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_label,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response.content or "",
            },
            "finish_reason": response.finish_reason or "stop",
        }],
        "usage": {
            "prompt_tokens": response.input_tokens,
            "completion_tokens": response.output_tokens,
            "total_tokens": response.input_tokens + response.output_tokens,
        },
    }
    if compat_openclaw_bridge:
        return payload

    payload["_aichaind"] = {
        "session_id": session.session_id if session else "",
        "routed_model": target_model,
        "routed_provider": target_provider,
        "provider_model": response.model,
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
        **routing_control_meta,
    }
    return payload


def _chunk_text_for_stream(content: str, chunk_size: int = 512) -> list[str]:
    text = str(content or "")
    if not text:
        return []
    return [text[index:index + chunk_size] for index in range(0, len(text), chunk_size)]


def _build_openai_stream_frames(payload: dict) -> list[str]:
    model = str(payload.get("model", "") or "")
    chunk_id = str(payload.get("id", "") or f"chatcmpl_{uuid.uuid4()}")
    created = int(payload.get("created", int(time.time())))
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    choice = choices[0] if choices else {}
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    content = str(message.get("content", "") or "")
    finish_reason = str(choice.get("finish_reason", "") or "stop")

    frames = [
        {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }],
        }
    ]
    for text_chunk in _chunk_text_for_stream(content):
        frames.append({
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": text_chunk},
                "finish_reason": None,
            }],
        })
    frames.append({
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": finish_reason,
        }],
    })
    return [f"data: {json.dumps(frame, ensure_ascii=False)}\n\n" for frame in frames] + ["data: [DONE]\n\n"]


def _provider_access_summary() -> dict:
    if not _provider_access_layer:
        return {}
    summary = {}
    for provider, decision in _provider_access_layer.summary().items():
        fallback_methods = decision.get("fallback_methods", [])
        summary[provider] = {
            "method": decision.get("selected_method", "disabled"),
            "status": decision.get("status", "disabled"),
            "reason": decision.get("reason", ""),
            "runtime_confirmed": bool(decision.get("runtime_confirmed", False)),
            "target_form_reached": bool(decision.get("target_form_reached", False)),
            "official_support": any(bool(option.get("official_support", False)) for option in decision.get("options", [])),
            "configured_methods": decision.get("configured_methods", []),
            "available_methods": decision.get("available_methods", []),
            "fallback_methods": fallback_methods,
            "fallback_path": " -> ".join(fallback_methods) if fallback_methods else "",
            "billing_basis": decision.get("billing_basis", ""),
            "usage_tracking": decision.get("usage_tracking", ""),
            "quota_visibility": decision.get("quota_visibility", ""),
            "limitations": decision.get("limitations", []),
            "project_verification": decision.get("project_verification", ""),
            "preferred_model": decision.get("preferred_model", ""),
            "verified_models": decision.get("verified_models", []),
            "target_model": decision.get("target_model", ""),
        }
    return summary


def _local_profile_summary() -> dict:
    if not _local_profile_store:
        return {}
    try:
        return _local_profile_store.summary(_roles.get('local_brain', ''))
    except Exception as exc:
        return {'error': str(exc)}


def _routing_preferences_summary() -> dict:
    providers = list(_routing_preferences.get('prepaid_premium_providers', [])) if isinstance(_routing_preferences, dict) else []
    return {
        'prefer_prepaid_premium': bool((_routing_preferences or {}).get('prefer_prepaid_premium', False)),
        'prepaid_premium_providers': sorted(providers),
    }


def _normalize_ui_origin(origin: str) -> str:
    value = str(origin or '').strip().rstrip('/')
    if not value:
        return ''
    parsed = urlparse(value)
    scheme = parsed.scheme or 'http'
    host = (parsed.hostname or '').strip().lower()
    port = parsed.port
    if not host or not port:
        return ''
    return f"{scheme}://{host}:{port}"


def _ui_cors_headers(origin: str) -> dict:
    normalized = _normalize_ui_origin(origin)
    if not normalized:
        return {}
    if normalized not in _OPENCLAW_UI_ORIGINS:
        return {}
    return {
        'Access-Control-Allow-Origin': normalized,
        'Access-Control-Allow-Headers': 'Content-Type, X-AIchain-Token',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Vary': 'Origin',
        'Cache-Control': 'no-store',
    }


def _is_trusted_ui_origin(origin: str) -> bool:
    return bool(_ui_cors_headers(origin))


def _is_trusted_ui_referer(referer: str) -> bool:
    if not referer:
        return False
    try:
        parsed = urlparse(referer)
    except Exception:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return _is_trusted_ui_origin(origin)


def _is_loopback_client(client_ip: str) -> bool:
    normalized = str(client_ip or "").strip()
    return normalized in {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


def _is_trusted_openclaw_provider_bridge(handler: BaseHTTPRequestHandler) -> bool:
    """
    Allow local OpenClaw provider calls to reach the OpenAI-compatible bridge
    without the sidecar's private X-AIchain-Token.

    OpenClaw's provider config for `aichain/dual-brain` uses a standard
    OpenAI-compatible `Authorization: Bearer ignore` request against
    http://127.0.0.1:8080/v1/chat/completions. That request originates from the
    local gateway process, not from a browser, so there is no trusted Origin
    header and there is no way for OpenClaw to inject the private sidecar token.
    """
    if not _is_loopback_client(getattr(handler, "client_address", ("",))[0]):
        return False
    authz = str(handler.headers.get("Authorization", "") or "").strip()
    if not authz.lower().startswith("bearer "):
        return False
    token = authz.split(" ", 1)[1].strip()
    return token.lower() == "ignore"


def _get_ui_session(session_id: str = ''):
    if not _session_store:
        return None
    resolved = str(session_id or '').strip() or DEFAULT_OPENCLAW_SESSION_ID
    session = _session_store.load(resolved)
    if session is None:
        session = _session_store.create(session_id=resolved)
    return session


def _recommended_premium_ui_choice(provider_access: dict) -> tuple[str, str, bool]:
    configured = set(_routing_preferences_summary().get('prepaid_premium_providers', []))
    candidates = []
    for provider, access in (provider_access or {}).items():
        method = str(access.get('method', '') or '').strip().lower()
        if method not in {'oauth', 'workspace_connector', 'enterprise_connector'}:
            continue
        if not access.get('runtime_confirmed'):
            continue
        billing_basis = str(access.get('billing_basis', '') or '').lower()
        implicit = any(token in billing_basis for token in ('subscription', 'entitlement', 'workspace', 'enterprise'))
        if provider not in configured and not implicit:
            continue
        model = str(access.get('preferred_model') or access.get('target_model') or '').strip()
        if not model and provider == 'openai-codex' and access.get('target_form_reached'):
            model = 'openai-codex/gpt-5.4'
        if not model:
            continue
        candidates.append((0 if access.get('target_form_reached') else 1, provider, model, bool(access.get('target_form_reached'))))
    if not candidates:
        return '', '', False
    candidates.sort()
    _, provider, model, target_reached = candidates[0]
    return provider, model, target_reached


def _recommended_ui_route(session) -> dict:
    provider_access = _provider_access_summary()
    routing_preference = _normalize_routing_preference(getattr(session, 'routing_preference', 'balanced') if session else 'balanced')
    reason_key = 'catalog_fallback'
    reason_text = 'Using the current best runtime-confirmed route after global catalog ranking and local availability checks.'
    if (
        session
        and getattr(session, 'routing_mode', 'auto') == 'manual'
        and getattr(session, 'locked_model', '')
        and _manual_lock_target_is_valid(getattr(session, 'locked_model', ''), getattr(session, 'locked_provider', ''))
    ):
        model = session.locked_model
        provider = getattr(session, 'locked_provider', '') or _infer_provider(model)
        reason_key = 'manual_lock'
        reason_text = 'You manually locked this model for the current session.'
    elif routing_preference == 'prefer_local' and _roles.get('local_brain'):
        model = _roles.get('local_brain', '')
        provider = _infer_provider(model)
        reason_key = 'prefer_local'
        reason_text = 'Local preference is active and a runtime-confirmed local model is available.'
    else:
        premium_provider, premium_model, target_reached = _recommended_premium_ui_choice(provider_access)
        if premium_provider and premium_model:
            model = premium_model
            provider = premium_provider
            reason_key = 'premium_entitlement'
            if target_reached:
                reason_text = 'A prepaid premium route is runtime-confirmed, so AIchain keeps maximum intelligence while marginal API cost stays near zero.'
            else:
                reason_text = 'Using the best currently verified premium model while the documented target model is not currently exposed.'
        elif _roles.get('fast_brain'):
            model = _roles.get('fast_brain', '')
            provider = _infer_provider(model)
            reason_key = 'fast_brain'
            reason_text = 'Falling back to the catalog fast brain because no stronger prepaid route is currently confirmed.'
        elif _roles.get('heavy_brain'):
            model = _roles.get('heavy_brain', '')
            provider = _infer_provider(model)
            reason_key = 'heavy_brain'
            reason_text = 'Using the catalog heavy brain because it is the best remaining runtime-confirmed route.'
        else:
            model = ''
            provider = ''
    access = provider_access.get(provider, {}) if provider else {}
    return {
        'label': model.split('/', 1)[-1] if model else '',
        'model': model,
        'provider': provider,
        'access_method': access.get('method', ''),
        'status': access.get('status', ''),
        'effective_cost_label': _effective_cost_label(provider, access),
        'why_key': reason_key,
        'why': reason_text,
        'fallback_path': access.get('fallback_path', ''),
    }


def _effective_cost_label(provider: str, access: dict) -> str:
    method = str(access.get('method', '') or '').strip().lower()
    billing = str(access.get('billing_basis', '') or '').strip().lower()
    if provider in _LOCAL_PROVIDERS or method == 'local':
        return 'zero API cost'
    if method == 'oauth' and any(flag in billing for flag in {'subscription', 'entitlement', 'workspace', 'enterprise'}):
        return 'marginal cost ~0'
    if method == 'api_key':
        return 'metered API'
    if method in {'workspace_connector', 'enterprise_connector'}:
        return 'connector-managed'
    return ''


def _ui_model_options(session) -> list[dict]:
    provider_access = _provider_access_summary()
    candidates = []
    premium_provider, premium_model, target_reached = _recommended_premium_ui_choice(provider_access)
    if premium_provider and premium_model:
        premium_badges = ['Premium', 'Best intelligence']
        if not target_reached:
            premium_badges.append('Verified fallback')
        candidates.append((premium_model, premium_provider, premium_badges))
    role_badges = {
        'fast_brain': ['Fast'],
        'heavy_brain': ['Heavy'],
        'visual_brain': ['Vision'],
        'local_brain': ['Local'],
    }
    for role, badges in role_badges.items():
        model = str(_roles.get(role, '') or '').strip()
        if model:
            candidates.append((model, _infer_provider(model), badges))
    if (
        session
        and getattr(session, 'routing_mode', 'auto') == 'manual'
        and getattr(session, 'locked_model', '')
        and _manual_lock_target_is_valid(getattr(session, 'locked_model', ''), getattr(session, 'locked_provider', ''))
    ):
        candidates.append((session.locked_model, getattr(session, 'locked_provider', '') or _infer_provider(session.locked_model), ['Locked']))

    seen = set()
    items = []
    for model, provider, badges in candidates:
        if not model or model in seen:
            continue
        seen.add(model)
        access = provider_access.get(provider, {}) if provider else {}
        access_method = access.get('method', '')
        if 'Locked' in badges:
            group = 'manual'
        elif provider in _LOCAL_PROVIDERS or access_method == 'local':
            group = 'local'
        elif access_method == 'oauth':
            group = 'premium_access'
        elif access_method in {'workspace_connector', 'enterprise_connector'}:
            group = 'workspace'
        else:
            group = 'api_access'
        items.append({
            'label': model.split('/', 1)[-1],
            'model': model,
            'provider': provider,
            'access_method': access_method,
            'status': access.get('status', ''),
            'effective_cost_label': _effective_cost_label(provider, access),
            'badges': badges,
            'group': group,
        })
    return items


def _build_ui_why_this_route(session, recommended_current: dict) -> dict:
    session_mode = getattr(session, 'routing_mode', 'auto') if session else 'auto'
    session_preference = _normalize_routing_preference(getattr(session, 'routing_preference', 'balanced') if session else 'balanced')
    bullets = []
    if session_mode == 'manual':
        bullets.append('Manual lock overrides automatic routing until you return to auto mode.')
    else:
        bullets.append('Global catalog remains first; local routing only narrows to routes you can actually use right now.')
    if session_preference != 'balanced':
        bullets.append(f'Active session preference: {session_preference}.')
    if recommended_current.get('effective_cost_label'):
        bullets.append(f'Current effective cost mode: {recommended_current.get("effective_cost_label")}.')
    if recommended_current.get('fallback_path'):
        bullets.append(f'Fallback path: {recommended_current.get("fallback_path")}.')
    return {
        'title': 'Why this model?',
        'summary': recommended_current.get('why', 'Current route selected from global ranking plus your runtime access state.'),
        'bullets': bullets,
    }


def _build_ui_savings_summary(recommended_current: dict, provider_access: dict) -> dict:
    provider = recommended_current.get('provider', '')
    access = provider_access.get(provider, {}) if provider else {}
    label = recommended_current.get('effective_cost_label', '')
    if label == 'marginal cost ~0':
        headline = 'Premium entitlement is currently saving API spend.'
        detail = 'AIchain is using a prepaid premium route instead of metered API billing while the entitlement remains runtime-confirmed.'
        kind = 'savings'
    elif label == 'zero API cost':
        headline = 'Local runtime avoids API billing.'
        detail = 'This route is using your local runtime instead of a metered provider.'
        kind = 'local'
    elif label == 'metered API':
        headline = 'Metered route selected for better overall value.'
        detail = 'AIchain chose a metered provider because it currently gives the best balance of intelligence, speed, stability and real availability.'
        kind = 'metered'
    else:
        headline = 'Effective cost is not fully classified yet.'
        detail = 'The route is valid, but cost mode is not fully described by the current runtime metadata.'
        kind = 'unknown'
    return {
        'kind': kind,
        'headline': headline,
        'detail': detail,
        'cost_mode': label,
        'quota_visibility': access.get('quota_visibility', ''),
    }


def _build_ui_control_state(session, last_confirmation: str = '') -> dict:
    provider_access = _provider_access_summary()
    recommended_current = _recommended_ui_route(session)
    return {
        'session': {
            'session_id': session.session_id if session else DEFAULT_OPENCLAW_SESSION_ID,
            'routing_mode': getattr(session, 'routing_mode', 'auto') if session else 'auto',
            'routing_preference': _normalize_routing_preference(getattr(session, 'routing_preference', 'balanced') if session else 'balanced'),
            'locked_model': getattr(session, 'locked_model', '') if session else '',
            'locked_provider': getattr(session, 'locked_provider', '') if session else '',
        },
        'recommended_current': recommended_current,
        'why_this_route': _build_ui_why_this_route(session, recommended_current),
        'savings_summary': _build_ui_savings_summary(recommended_current, provider_access),
        'provider_access': provider_access,
        'local_profiles': _local_profile_summary(),
        'routing_preferences': _routing_preferences_summary(),
        'model_options': _ui_model_options(session),
        'last_confirmation': last_confirmation,
    }


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
        available_models = []
        if getattr(codex_access, "verified_models", None):
            available_models.extend(list(codex_access.verified_models or []))
        preferred_model = str(getattr(codex_access, "preferred_model", "") or "").strip()
        if preferred_model:
            available_models.append(preferred_model)
        target_model_hint = str(getattr(codex_access, "target_model", "") or "").strip()
        if target_model_hint:
            available_models.append(target_model_hint)
        available_models = list(dict.fromkeys(model for model in available_models if model))
        resolved_model = (
            codex_adapter.resolve_preferred_model(target_model, available_models)
            if available_models else
            codex_adapter.resolve_preferred_model(target_model)
        )
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

def _ensure_provider_access(decision, payload: dict, target_model: str, target_provider: str, balance_report, allow_failover: bool = True):
    provider = (target_provider or _infer_provider(target_model)).lower()
    if not _provider_access_layer:
        return decision, target_model, provider, None, False, ""

    access_decision = _provider_access_layer.resolve(provider)
    runtime_ready = True
    runtime_reason = ""
    if provider in _LOCAL_PROVIDERS:
        adapter = get_adapter(provider) or get_adapter_for_model(target_model)
        runtime_ready, runtime_reason = _local_runtime_ready(provider, adapter)
        if not runtime_ready:
            access_decision = _provider_access_layer.resolve(provider)
    if (
        access_decision.selected_method != "disabled"
        and (
            access_decision.status != "target_form_not_reached"
            or access_decision.runtime_confirmed
        )
        and runtime_ready
    ):
        return decision, target_model, provider, access_decision, False, ""

    cost_optimizer = getattr(_cascade_router, "_cost_optimizer", None)
    if not allow_failover or not cost_optimizer or not balance_report:
        return (
            decision,
            target_model,
            provider,
            access_decision,
            False,
            f"provider_access_unavailable:{provider}:{runtime_reason or access_decision.reason}",
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
            f"{decision.reason}|access_failover:{provider}:{runtime_reason or access_decision.reason}->{alt.reason}"
            if decision.reason else f"access_failover:{provider}:{runtime_reason or access_decision.reason}->{alt.reason}"
        )
        return decision, alt.model, alt_provider, alt_access, True, ""

    return (
        decision,
        target_model,
        provider,
        access_decision,
        False,
        f"provider_access_unavailable:{provider}:{runtime_reason or access_decision.reason}",
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


def _provider_timeout_ms(provider_name: str, payload: dict, messages: list[dict] | None = None) -> int:
    normalized = str(provider_name or "").strip().lower()
    max_tokens = _effective_max_tokens(payload, messages)
    if normalized == "openai-codex":
        prompt_text = _last_user_prompt_text(messages or []) or _payload_prompt_text(payload)
        hint = prompt_text.lower()
        if any(token in hint for token in ("code", "coding", "refactor", "debug", "unit test", "unit_test", "function", "script", "endpoint", "api", "sql", "patch", "repository", "repo")):
            return max(90000, min(140000, 70000 + (max_tokens * 350)))
        if any(token in hint for token in ("reason", "reasoning", "analysis", "security", "proof", "theorem", "research", "math", "password", "credential", "login", "secret", "token", "auth")):
            return max(80000, min(130000, 65000 + (max_tokens * 325)))
        if any(token in hint for token in ("json", "schema", "structured", "extract", "yaml", "xml", "csv")):
            return max(55000, min(90000, 45000 + (max_tokens * 250)))
        return max(45000, min(75000, 35000 + (max_tokens * 200)))
    return 0


def _payload_prompt_text(payload: dict) -> str:
    messages = payload.get("messages") or []
    parts: list[str] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or part.get("content") or ""
                    if isinstance(text, str):
                        parts.append(text)
    return " ".join(part for part in parts if part)


def _last_user_prompt_text(messages: list[dict]) -> str:
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or part.get("content") or ""
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return " ".join(parts).strip()
    return ""


def _request_shape(messages: list[dict], payload: dict | None = None) -> str:
    if detect_visual_content(messages or []):
        return "visual"

    prompt_text = _last_user_prompt_text(messages or []) or _payload_prompt_text(payload or {})
    normalized = prompt_text.lower()
    word_count = len(prompt_text.split())

    if _STRUCTURED_PROMPT_RE.search(normalized):
        return "structured"
    if detect_coding_intent(prompt_text):
        return "coding"
    if _CREATIVE_PROMPT_RE.search(normalized):
        return "creative"
    if _REASONING_PROMPT_RE.search(normalized):
        return "reasoning"

    complexity, confidence = estimate_complexity(prompt_text)
    if complexity == "analyst" and confidence >= 0.7:
        return "reasoning"
    if word_count <= 18 and confidence >= 0.6:
        return "simple"
    return "general"


def _default_max_tokens_for_shape(shape: str) -> int:
    return {
        "simple": 64,
        "structured": 160,
        "general": 256,
        "visual": 320,
        "reasoning": 480,
        "creative": 640,
        "coding": 900,
    }.get(shape, 256)


def _effective_max_tokens(payload: dict, messages: list[dict] | None = None) -> int:
    raw = payload.get("max_tokens")
    if raw is None:
        return _default_max_tokens_for_shape(_request_shape(messages or payload.get("messages") or [], payload))
    try:
        value = int(raw)
    except Exception:
        value = 0
    if value > 0:
        return value
    return _default_max_tokens_for_shape(_request_shape(messages or payload.get("messages") or [], payload))


def _apply_response_style_overrides(messages: list[dict], payload: dict) -> list[dict]:
    shape = _request_shape(messages or [], payload)
    if shape != "simple":
        return messages
    brevity_prompt = {
        "role": "system",
        "content": "Respond briefly in 1-2 sentences unless the user explicitly asks for more detail.",
    }
    updated = list(messages or [])
    insert_at = 1 if updated and updated[0].get("role") == "system" else 0
    updated.insert(insert_at, brevity_prompt)
    return updated


def _local_runtime_ready(provider_name: str, adapter) -> tuple[bool, str]:
    normalized = str(provider_name or "").strip().lower()
    if normalized not in _LOCAL_PROVIDERS:
        return True, ""
    cache_key = f"{normalized}:{getattr(adapter, 'base_url', '')}"
    now = time.time()
    cached = _LOCAL_RUNTIME_HEALTH_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _LOCAL_RUNTIME_HEALTH_TTL_SECONDS:
        return cached[1], cached[2]

    healthy = False
    reason = "runtime_health_check_failed"
    try:
        healthy = bool(adapter and adapter.health_check())
        if healthy:
            reason = ""
    except Exception as exc:
        reason = f"runtime_health_check_failed:{type(exc).__name__}"

    _LOCAL_RUNTIME_HEALTH_CACHE[cache_key] = (now, healthy, reason)
    if _provider_access_layer:
        try:
            _provider_access_layer.mark_runtime_result(
                normalized,
                healthy,
                reason or "health_check:ok",
                target_form_reached=healthy,
            )
        except Exception:
            pass
    return healthy, reason


def _refresh_access_decision_for_provider(provider_name: str, access_decision):
    if not _provider_access_layer or not provider_name:
        return access_decision
    try:
        return _provider_access_layer.resolve(provider_name)
    except Exception:
        return access_decision


def _build_request(payload: dict, messages: list[dict], target_model: str, adapter) -> CompletionRequest:
    extra = {}
    provider_name = str(getattr(adapter, "name", "") or "").lower()
    provider_timeout_ms = _provider_timeout_ms(provider_name, payload, messages)
    if provider_timeout_ms:
        extra["timeout_ms"] = provider_timeout_ms
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
        messages=_apply_response_style_overrides(messages, payload),
        max_tokens=_effective_max_tokens(payload, messages),
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
    allow_failover: bool = True,
):
    cost_optimizer = getattr(_cascade_router, "_cost_optimizer", None)
    if not allow_failover or not cost_optimizer or not balance_report or not _should_retry_provider_error(response):
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


def _looks_like_code_generation_request(task_hint: str) -> bool:
    hint = str(task_hint or "").lower()
    if not hint:
        return False
    tokens = (
        "code", "coding", "program", "implement", "build", "create", "develop",
        "function", "class", "script", "module", "endpoint", "api", "database",
        "schema", "migration", "python", "javascript", "typescript", "java",
        "rust", "sql", "game", "tetris", "pygame", "godot", "unity", "unit test",
        "integration test", "refactor", "debug", "bug", "fix", "patch",
    )
    return any(token in hint for token in tokens)


def _validate_output(content: str, task_hint: str = "") -> dict:
    if _DANGEROUS_PATTERNS.search(content):
        return {"safe": False, "reason": "dangerous_pattern_detected"}
    if _CODE_SENSITIVE_PATTERNS.search(content) and not _looks_like_code_generation_request(task_hint):
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
    routing_preferences: dict | None = None,
):
    global _auth_manager, _rate_limiter, _cascade_router, _audit_logger
    global _policy_engine, _controller, _session_store, _pii_redactor
    global _roles, _version, _balance_checker, _discovery_report
    global _route_eval_collector, _summarizer, _injection_guard, _provider_access_layer, _local_profile_store, _input_redaction_enabled, _routing_preferences

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
    _routing_preferences = dict(routing_preferences or {})

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











