#!/usr/bin/env python3
"""
aichaind.main — Daemon Entry Point

The aichaind sidecar orchestration daemon.
Boots all subsystems and starts the HTTP server.
"""

import os
import sys
import logging
import signal
from pathlib import Path

from aichaind.core.state_machine import load_config, get_paths, Controller
from aichaind.core.session import SessionStore
from aichaind.core.policy import PolicyEngine
from aichaind.security.auth import AuthTokenManager
from aichaind.security.rate_limiter import TokenBucketRateLimiter
from aichaind.routing.cascade import CascadeRouter
from aichaind.routing.cost_optimizer import CostOptimizer
from aichaind.routing.table_sync import fetch_routing_table
from aichaind.providers.balance import BalanceChecker
from aichaind.telemetry.audit import AuditLogger
from aichaind.transport.http_server import start_server


VERSION = "5.0.0-alpha"
BANNER = r"""
     _    ___ ____ _           _           _
    / \  |_ _/ ___| |__   __ _(_)_ __   __| |
   / _ \  | | |   | '_ \ / _` | | '_ \ / _` |
  / ___ \ | | |___| | | | (_| | | | | | (_| |
 /_/   \_|___\____|_| |_|\__,_|_|_| |_|\__,_|  v5.0 — Sidecar Daemon
"""

_LOCAL_BASE_ENV = {
    "local": "AICHAIN_LOCAL_BASE_URL",
    "vllm": "VLLM_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
    "lmstudio": "LMSTUDIO_BASE_URL",
    "llamacpp": "LLAMACPP_BASE_URL",
}


def resolve_roles(cfg: dict, log: logging.Logger) -> tuple[dict, dict]:
    """Fetch routing table and assign Fast/Heavy/Visual brain roles."""
    roles = {
        "fast_brain": "openrouter/google/gemini-2.5-flash:free",
        "heavy_brain": "openrouter/google/gemini-2.5-pro",
        "visual_brain": "openrouter/openai/gpt-4o",
        "local_brain": "",
    }

    url = cfg.get("routing_url", "")
    if not url:
        log.warning("No routing_url configured — using default roles")
        return roles, {}

    table = fetch_routing_table(url, log, cfg.get("version_compat"))
    if not table or "routing_hierarchy" not in table:
        log.warning("Routing table unavailable — using default roles")
        return roles, table or {}

    contract_roles = ((table.get("_aichaind_contract") or {}).get("roles") or {})
    if contract_roles:
        if contract_roles.get("fast"):
            roles["fast_brain"] = contract_roles["fast"]
        if contract_roles.get("heavy"):
            roles["heavy_brain"] = contract_roles["heavy"]
        if contract_roles.get("visual"):
            roles["visual_brain"] = contract_roles["visual"]
        log.info(
            "Roles sourced from catalog contract: "
            f"fast={roles['fast_brain']}, heavy={roles['heavy_brain']}, visual={roles['visual_brain']}"
        )
        return roles, table

    hierarchy = table["routing_hierarchy"]

    for model_entry in hierarchy:
        if model_entry.get("tier") in ("FREE_FRONTIER", "OAUTH_BRIDGE") or \
           model_entry.get("metrics", {}).get("cost", 1) <= 0.0:
            roles["fast_brain"] = model_entry["model"]
            break

    heavy_hitter = table.get("heavy_hitter", {}).get("model")
    if heavy_hitter and heavy_hitter != "N/A":
        roles["heavy_brain"] = heavy_hitter
    elif hierarchy:
        roles["heavy_brain"] = max(
            hierarchy,
            key=lambda item: item.get("metrics", {}).get("intelligence", 0)
        )["model"]

    for model_entry in hierarchy:
        mid = model_entry.get("model", "")
        if any(token in mid.lower() for token in ("gpt-4o", "gemini", "vl", "vision")):
            roles["visual_brain"] = mid
            break

    return roles, table


def resolve_local_role(cfg: dict, log: logging.Logger) -> str:
    """Resolve a local execution fallback model if configured and reachable."""
    local_cfg = cfg.get("local_execution", {})
    if not isinstance(local_cfg, dict) or not local_cfg.get("enabled"):
        return ""

    provider = str(local_cfg.get("provider") or "local").lower()
    model = str(local_cfg.get("default_model") or "").strip()
    base_url = str(local_cfg.get("base_url") or "").strip()
    require_healthcheck = bool(local_cfg.get("require_healthcheck", True))

    if not model:
        log.warning("Local execution enabled but no default_model configured")
        return ""

    env_var = _LOCAL_BASE_ENV.get(provider, "AICHAIN_LOCAL_BASE_URL")
    if base_url:
        os.environ.setdefault(env_var, base_url)

    if "/" not in model:
        model = f"{provider}/{model}"

    from aichaind.providers.registry import get_adapter

    adapter = get_adapter(provider)
    if not adapter:
        log.warning(f"Local execution configured but adapter missing for provider={provider}")
        return ""

    if require_healthcheck and not adapter.health_check():
        log.warning(
            f"Local execution configured but health check failed for provider={provider}; local reroute disabled"
        )
        return ""

    log.info(f"Local execution: ACTIVE ({model} via {provider})")
    return model


def discover_provider_capabilities(discovery_report, log: logging.Logger) -> dict[str, set[str]]:
    """Probe direct providers once on boot and cache available model IDs."""
    from aichaind.providers.registry import get_adapter

    capabilities: dict[str, set[str]] = {}
    for credential in discovery_report.credentials:
        provider = credential.provider
        try:
            adapter = get_adapter(provider)
            if not adapter or adapter.name != provider:
                capabilities[provider] = set()
                log.info(f"Capability discovery: {provider} skipped (no direct adapter)")
                continue
            result = adapter.discover()
            models = set(result.available_models or [])
            capabilities[provider] = models
            log.info(f"Capability discovery: {provider} status={result.status} models={len(models)}")
        except Exception as exc:
            log.warning(f"Capability discovery failed for {provider}: {exc}")
            capabilities[provider] = set()
    return capabilities


def main():
    """Boot the aichaind sidecar daemon."""
    print(BANNER)

    config_path = None
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    cfg = load_config(config_path)
    paths = get_paths(cfg)
    paths["data_dir"].mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [aichaind] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(paths["log_file"], encoding="utf-8"),
        ],
    )
    log = logging.getLogger("aichaind")
    log.info(f"aichaind v{VERSION} starting...")

    auth_manager = AuthTokenManager(paths["auth_token_file"])
    auth_manager.generate_token()
    log.info(f"Auth token written to {paths['auth_token_file']}")

    rate_limiter = TokenBucketRateLimiter(
        rate=cfg.get("rate_limit_per_minute", 60),
        burst=cfg.get("rate_limit_burst", 10),
    )

    policy_engine = PolicyEngine(cfg.get("policy", {}))

    controller = Controller(cfg, log)
    log.info(f"State machine: {controller.state.get('system')} / {controller.state.get('circuit')}")

    session_store = SessionStore(paths["session_dir"])

    audit_logger = AuditLogger(paths["audit_file"])
    audit_logger.record("daemon_start", {"version": VERSION})

    from aichaind.providers.discovery import discover_providers, inject_keys_into_env
    discovery_report = discover_providers()
    inject_keys_into_env(discovery_report)
    log.info(f"Providers discovered: {len(discovery_report.credentials)} "
             f"(direct: {', '.join(discovery_report.direct_providers) or 'none'})")
    for cred in discovery_report.credentials:
        sub_tag = " [SUBSCRIPTION]" if cred.has_subscription else ""
        log.info(f"  {cred.provider}: priority={cred.priority}{sub_tag}")

    roles, routing_table = resolve_roles(cfg, log)
    roles["local_brain"] = resolve_local_role(cfg, log)
    log.info(f"Fast Brain:  {roles['fast_brain']}")
    log.info(f"Heavy Brain: {roles['heavy_brain']}")
    log.info(f"Visual:      {roles['visual_brain']}")
    log.info(f"Local:       {roles.get('local_brain') or 'N/A'}")

    routing_cfg = cfg.get("routing", {})
    routing_cfg["layer3_enabled"] = True
    routing_cfg["layer4_enabled"] = bool(roles.get("fast_brain"))
    cascade_router = CascadeRouter(routing_cfg)

    balance_checker = BalanceChecker()
    cost_optimizer = CostOptimizer(routing_table)
    provider_capabilities = discover_provider_capabilities(discovery_report, log)
    cost_optimizer.configure_provider_capabilities(provider_capabilities)
    cascade_router.configure_cost_optimizer(cost_optimizer)
    log.info("Cost optimizer: ACTIVE")

    if cascade_router._layer4_enabled and roles.get("fast_brain"):
        from aichaind.providers.registry import get_adapter
        try:
            l4_adapter = get_adapter(roles["fast_brain"].split("/", 1)[0])
            if not l4_adapter:
                l4_adapter = get_adapter(roles["fast_brain"])
            cascade_router.configure_cloud(l4_adapter, roles["fast_brain"])
            log.info(f"Layer 4 cloud classifier: ACTIVE (model={roles['fast_brain']})")
        except Exception as exc:
            log.warning(f"Layer 4 disabled: {exc}")
            cascade_router._layer4_enabled = False

    from aichaind.telemetry.route_eval import RouteEvalCollector
    route_eval_collector = RouteEvalCollector(paths["data_dir"] / "route_eval.jsonl")
    log.info("Route eval: ACTIVE")

    from aichaind.core.summarizer import ContextSummarizer
    summarizer = ContextSummarizer()
    log.info("Structured summarizer: ACTIVE")

    from aichaind.security.redactor import PIIRedactor
    pii_redactor = PIIRedactor()
    log.info("PII redactor: ACTIVE")

    from aichaind.security.injection_guard import PromptInjectionGuard
    injection_guard = PromptInjectionGuard()
    log.info("Injection guard: ACTIVE")

    port = cfg.get("port", 8080)
    httpd = start_server(
        port=port,
        auth_manager=auth_manager,
        rate_limiter=rate_limiter,
        cascade_router=cascade_router,
        audit_logger=audit_logger,
        policy_engine=policy_engine,
        controller=controller,
        session_store=session_store,
        pii_redactor=pii_redactor,
        injection_guard=injection_guard,
        roles=roles,
        version=VERSION,
        balance_checker=balance_checker,
        discovery_report=discovery_report,
        route_eval_collector=route_eval_collector,
        summarizer=summarizer,
    )

    def shutdown(signum, frame):
        log.info("Shutdown signal received...")
        audit_logger.record("daemon_stop", {"signal": signum})
        auth_manager.revoke()
        httpd.server_close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        shutdown(signal.SIGINT, None)


if __name__ == "__main__":
    main()
