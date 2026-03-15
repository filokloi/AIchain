#!/usr/bin/env python3
"""
aichaind.providers.access — Provider Access Layer

Separates provider access concerns from routing and adapter execution.

Supported access methods:
- local runtimes
- API key access
- officially supported OAuth/sign-in (only when explicitly configured)
- workspace / enterprise connectors (only when explicitly configured)
- disabled / fallback semantics when a method is not allowed or not available

The layer is intentionally conservative:
- API key and local runtimes are first-class execution paths
- OAuth is never assumed from consumer subscriptions or key prefixes
- unsupported or unstable access modes are represented, but not selected
- execution can fail over to another provider when the chosen provider has no
  allowed and usable access method
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ACCESS_LOCAL = "local"
ACCESS_API_KEY = "api_key"
ACCESS_OAUTH = "oauth"
ACCESS_WORKSPACE = "workspace_connector"
ACCESS_ENTERPRISE = "enterprise_connector"
ACCESS_DISABLED = "disabled"

_RUNTIME_METHODS = {ACCESS_LOCAL, ACCESS_API_KEY, ACCESS_OAUTH}
_DEFAULT_ORDER = [ACCESS_LOCAL, ACCESS_API_KEY, ACCESS_WORKSPACE, ACCESS_ENTERPRISE, ACCESS_OAUTH]

_SUPPORTED_API_KEY_PROVIDERS = {
    "openrouter", "openai", "google", "anthropic", "deepseek", "groq",
    "mistral", "xai", "cohere", "moonshot", "zhipu",
}
_SPECIAL_ACCESS_PROVIDERS = {"openai-codex"}
_LOCAL_PROVIDERS = {"local", "vllm", "ollama", "lmstudio", "llamacpp"}
_ALL_KNOWN_PROVIDERS = sorted(_SUPPORTED_API_KEY_PROVIDERS | _LOCAL_PROVIDERS | _SPECIAL_ACCESS_PROVIDERS)


def _configured_local_providers(local_cfg: dict) -> set[str]:
    if not _bool((local_cfg or {}).get("enabled")):
        return set()
    configured = set()
    provider = str((local_cfg or {}).get("provider") or "local").strip().lower()
    auto_detect = _bool((local_cfg or {}).get("auto_detect"), False) or provider == "auto"
    preferred = [
        str(item).strip().lower()
        for item in ((local_cfg or {}).get("preferred_providers") or [])
        if str(item).strip()
    ]
    if provider and provider != "auto":
        configured.add(provider)
    if auto_detect:
        configured.update(item for item in preferred if item in _LOCAL_PROVIDERS)
        if not configured:
            configured.update(_LOCAL_PROVIDERS)
    return configured


def _bool(value, default=False):
    if value is None:
        return default
    return bool(value)


@dataclass
class ProviderAccessOption:
    provider: str
    method: str
    configured: bool = False
    enabled: bool = True
    official_support: bool = False
    technically_stable: bool = True
    provider_compliant: bool = True
    adapter_enabled: bool = False
    available: bool = False
    priority: int = 100
    source: str = ""
    detail: str = ""
    health_checked: bool = False
    healthy: bool = False
    billing_basis: str = ""
    usage_tracking: str = ""
    quota_visibility: str = ""
    limitations: list[str] = field(default_factory=list)
    project_verification: str = ""

    @property
    def selectable(self) -> bool:
        return (
            self.enabled
            and self.configured
            and self.official_support
            and self.technically_stable
            and self.provider_compliant
            and self.adapter_enabled
            and self.available
        )


@dataclass
class ProviderAccessDecision:
    provider: str
    selected_method: str = ACCESS_DISABLED
    status: str = "disabled"
    reason: str = "no_supported_access_method"
    configured_methods: list[str] = field(default_factory=list)
    available_methods: list[str] = field(default_factory=list)
    fallback_methods: list[str] = field(default_factory=list)
    runtime_confirmed: bool = False
    target_form_reached: bool = False
    billing_basis: str = ""
    usage_tracking: str = ""
    quota_visibility: str = ""
    limitations: list[str] = field(default_factory=list)
    project_verification: str = ""
    preferred_model: str = ""
    verified_models: list[str] = field(default_factory=list)
    target_model: str = ""
    options: list[ProviderAccessOption] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "selected_method": self.selected_method,
            "status": self.status,
            "reason": self.reason,
            "configured_methods": list(self.configured_methods),
            "available_methods": list(self.available_methods),
            "fallback_methods": list(self.fallback_methods),
            "runtime_confirmed": bool(self.runtime_confirmed),
            "target_form_reached": bool(self.target_form_reached),
            "billing_basis": self.billing_basis,
            "usage_tracking": self.usage_tracking,
            "quota_visibility": self.quota_visibility,
            "limitations": list(self.limitations),
            "project_verification": self.project_verification,
            "preferred_model": self.preferred_model,
            "verified_models": list(self.verified_models),
            "target_model": self.target_model,
            "options": [
                {
                    "method": option.method,
                    "configured": option.configured,
                    "enabled": option.enabled,
                    "official_support": option.official_support,
                    "technically_stable": option.technically_stable,
                    "provider_compliant": option.provider_compliant,
                    "adapter_enabled": option.adapter_enabled,
                    "available": option.available,
                    "priority": option.priority,
                    "source": option.source,
                    "detail": option.detail,
                    "health_checked": option.health_checked,
                    "healthy": option.healthy,
                    "billing_basis": option.billing_basis,
                    "usage_tracking": option.usage_tracking,
                    "quota_visibility": option.quota_visibility,
                    "limitations": list(option.limitations),
                    "project_verification": option.project_verification,
                }
                for option in self.options
            ],
        }


class ProviderAccessLayer:
    def __init__(self, decisions: dict[str, ProviderAccessDecision], default_order: list[str] | None = None):
        self._decisions = decisions
        self.default_order = list(default_order or _DEFAULT_ORDER)

    def resolve(self, provider: str) -> ProviderAccessDecision:
        normalized = (provider or "").strip().lower()
        return self._decisions.get(normalized, ProviderAccessDecision(provider=normalized or "unknown"))

    def runtime_providers(self) -> list[str]:
        return [
            provider
            for provider, decision in self._decisions.items()
            if decision.selected_method in _RUNTIME_METHODS
        ]

    def mark_runtime_result(
        self,
        provider: str,
        confirmed: bool,
        reason: str = "",
        target_form_reached: bool | None = None,
        preferred_model: str = "",
        verified_models: list[str] | None = None,
        target_model: str = "",
    ) -> None:
        decision = self.resolve(provider)
        if decision.selected_method == ACCESS_DISABLED:
            return

        if preferred_model:
            decision.preferred_model = str(preferred_model)
        if verified_models is not None:
            decision.verified_models = [str(item) for item in verified_models if str(item).strip()]
        if target_model:
            decision.target_model = str(target_model)

        decision.runtime_confirmed = bool(confirmed)
        decision.target_form_reached = bool(
            confirmed if target_form_reached is None else target_form_reached
        )

        if confirmed:
            decision.status = (
                "runtime_confirmed"
                if decision.target_form_reached
                else "target_form_not_reached"
            )
            if reason:
                decision.reason = reason
            return

        if decision.selected_method in _RUNTIME_METHODS:
            decision.status = "target_form_not_reached"
            if reason:
                decision.reason = reason


    def summary(self) -> dict[str, dict]:
        return {provider: decision.to_dict() for provider, decision in sorted(self._decisions.items())}


@dataclass
class _ConnectionHints:
    oauth_profiles: dict[str, str] = field(default_factory=dict)
    workspace_connectors: dict[str, str] = field(default_factory=dict)
    enterprise_connectors: dict[str, str] = field(default_factory=dict)


def build_provider_access_layer(cfg: dict, discovery_report, log=None) -> ProviderAccessLayer:
    provider_cfg = ((cfg or {}).get("provider_access") or {})
    providers_cfg = provider_cfg.get("providers") or {}
    default_order = provider_cfg.get("default_order") or _DEFAULT_ORDER
    local_cfg = (cfg or {}).get("local_execution") or {}
    hints = _load_connection_hints(Path((cfg or {}).get("openclaw_config", "~/.openclaw/openclaw.json")).expanduser())

    credentials_by_provider = {
        (cred.provider or "").strip().lower(): cred
        for cred in getattr(discovery_report, "credentials", [])
    }

    known_providers = set(_ALL_KNOWN_PROVIDERS)
    known_providers.update(credentials_by_provider.keys())
    known_providers.update(hints.oauth_profiles.keys())
    known_providers.update(hints.workspace_connectors.keys())
    known_providers.update(hints.enterprise_connectors.keys())

    known_providers.update(_configured_local_providers(local_cfg))

    decisions: dict[str, ProviderAccessDecision] = {}
    for provider in sorted(known_providers):
        overrides = providers_cfg.get(provider) or {}
        options = _build_options_for_provider(
            provider=provider,
            overrides=overrides,
            credentials_by_provider=credentials_by_provider,
            local_cfg=local_cfg,
            hints=hints,
        )
        decisions[provider] = _select_option(provider, options, overrides, default_order)

    layer = ProviderAccessLayer(decisions=decisions, default_order=default_order)
    if log:
        enabled = [p for p, d in decisions.items() if d.selected_method != ACCESS_DISABLED]
        disabled = [p for p, d in decisions.items() if d.selected_method == ACCESS_DISABLED]
        log.info(f"Provider access layer: enabled={len(enabled)} disabled={len(disabled)}")
        for provider in enabled:
            decision = decisions[provider]
            log.info(
                f"  access {provider}: method={decision.selected_method} status={decision.status} reason={decision.reason}"
            )
        for provider in disabled:
            decision = decisions[provider]
            if provider in credentials_by_provider or provider in hints.oauth_profiles or provider in hints.workspace_connectors or provider in hints.enterprise_connectors:
                log.info(f"  access {provider}: DISABLED ({decision.reason})")
    return layer


def _metadata_from_cfg(method_cfg: dict) -> dict:
    method_cfg = method_cfg or {}
    return {
        "billing_basis": str(method_cfg.get("billing_basis") or ""),
        "usage_tracking": str(method_cfg.get("usage_tracking") or ""),
        "quota_visibility": str(method_cfg.get("quota_visibility") or ""),
        "limitations": [str(item) for item in (method_cfg.get("limitations") or []) if str(item).strip()],
        "project_verification": str(method_cfg.get("project_verification") or ""),
    }

def _build_options_for_provider(
    provider: str,
    overrides: dict,
    credentials_by_provider: dict,
    local_cfg: dict,
    hints: _ConnectionHints,
) -> list[ProviderAccessOption]:
    options: list[ProviderAccessOption] = []

    enabled_methods = set(overrides.get("enabled_methods") or _DEFAULT_ORDER)
    disabled_methods = set(overrides.get("disabled_methods") or [])

    def method_allowed(method: str) -> bool:
        return method in enabled_methods and method not in disabled_methods

    if provider in _LOCAL_PROVIDERS:
        configured = provider in _configured_local_providers(local_cfg)
        local_meta = _metadata_from_cfg(overrides.get("local") or {})
        options.append(ProviderAccessOption(
            provider=provider,
            method=ACCESS_LOCAL,
            configured=configured,
            enabled=method_allowed(ACCESS_LOCAL),
            official_support=True,
            technically_stable=_bool((overrides.get("local") or {}).get("technically_stable"), True),
            provider_compliant=True,
            adapter_enabled=True,
            available=configured,
            priority=0,
            source="local_execution",
            detail=str(local_cfg.get("base_url") or ""),
            health_checked=False,
            healthy=False,
            **local_meta,
        ))

    credential = credentials_by_provider.get(provider)
    api_key_meta = _metadata_from_cfg(overrides.get("api_key") or {})
    options.append(ProviderAccessOption(
        provider=provider,
        method=ACCESS_API_KEY,
        configured=credential is not None,
        enabled=method_allowed(ACCESS_API_KEY),
        official_support=provider in _SUPPORTED_API_KEY_PROVIDERS,
        technically_stable=_bool((overrides.get("api_key") or {}).get("technically_stable"), True),
        provider_compliant=_bool((overrides.get("api_key") or {}).get("provider_compliant"), True),
        adapter_enabled=provider in _SUPPORTED_API_KEY_PROVIDERS,
        available=credential is not None,
        priority=10,
        source=getattr(credential, "source", "") if credential else "",
        detail="subscription_detected" if getattr(credential, "has_subscription", False) else "",
        **api_key_meta,
    ))

    oauth_cfg = overrides.get("oauth") or {}
    oauth_meta = _metadata_from_cfg(oauth_cfg)
    oauth_profile = hints.oauth_profiles.get(provider, oauth_cfg.get("profile", ""))
    oauth_official = _bool(oauth_cfg.get("official_support"), False)
    oauth_adapter_enabled = _bool(oauth_cfg.get("adapter_enabled"), False)
    options.append(ProviderAccessOption(
        provider=provider,
        method=ACCESS_OAUTH,
        configured=bool(oauth_profile),
        enabled=method_allowed(ACCESS_OAUTH),
        official_support=oauth_official,
        technically_stable=_bool(oauth_cfg.get("technically_stable"), False),
        provider_compliant=_bool(oauth_cfg.get("provider_compliant"), oauth_official),
        adapter_enabled=oauth_adapter_enabled,
        available=bool(oauth_profile and oauth_official and oauth_adapter_enabled),
        priority=40,
        source="openclaw_auth_profile" if oauth_profile else "",
        detail=str(oauth_profile or ""),
        **oauth_meta,
    ))

    for method, mapping, cfg_key, priority in (
        (ACCESS_WORKSPACE, hints.workspace_connectors, "workspace_connector", 20),
        (ACCESS_ENTERPRISE, hints.enterprise_connectors, "enterprise_connector", 30),
    ):
        method_cfg = overrides.get(cfg_key) or {}
        connector_meta = _metadata_from_cfg(method_cfg)
        connector_name = mapping.get(provider, method_cfg.get("name", ""))
        official_support = _bool(method_cfg.get("official_support"), False)
        adapter_enabled = _bool(method_cfg.get("adapter_enabled"), False)
        options.append(ProviderAccessOption(
            provider=provider,
            method=method,
            configured=bool(connector_name),
            enabled=method_allowed(method),
            official_support=official_support,
            technically_stable=_bool(method_cfg.get("technically_stable"), False),
            provider_compliant=_bool(method_cfg.get("provider_compliant"), official_support),
            adapter_enabled=adapter_enabled,
            available=bool(connector_name and official_support and adapter_enabled),
            priority=priority,
            source="openclaw_connector" if connector_name else "",
            detail=str(connector_name or ""),
            **connector_meta,
        ))

    return options


def _select_option(provider: str, options: list[ProviderAccessOption], overrides: dict, default_order: list[str]) -> ProviderAccessDecision:
    preferred_order = overrides.get("preferred_order") or default_order
    descriptive_option = _select_descriptive_option(options, preferred_order)
    configured_methods = [option.method for option in options if option.configured]
    available_methods = [option.method for option in options if option.selectable]
    fallback_methods = []

    method_rank = {method: index for index, method in enumerate(preferred_order)}
    selectable = sorted(
        [option for option in options if option.selectable],
        key=lambda option: (method_rank.get(option.method, 999), option.priority, option.method),
    )

    disabled_reasons = []
    for option in options:
        if option.selectable:
            continue
        if option.method == ACCESS_API_KEY and option.configured and not option.official_support:
            disabled_reasons.append(f"{option.method}:provider_does_not_support_api_key")
        elif option.configured and not option.enabled:
            disabled_reasons.append(f"{option.method}:disabled_by_config")
        elif option.configured and not option.official_support:
            disabled_reasons.append(f"{option.method}:not_officially_supported")
        elif option.configured and not option.technically_stable:
            disabled_reasons.append(f"{option.method}:not_stable")
        elif option.configured and not option.provider_compliant:
            disabled_reasons.append(f"{option.method}:not_provider_compliant")
        elif option.configured and not option.adapter_enabled:
            disabled_reasons.append(f"{option.method}:adapter_not_enabled")
        elif option.method == ACCESS_LOCAL and option.enabled and option.configured and not option.available:
            disabled_reasons.append(f"{option.method}:local_runtime_unavailable")

    if selectable:
        selected = selectable[0]
        fallback_methods = [option.method for option in selectable[1:]]
        return ProviderAccessDecision(
            provider=provider,
            selected_method=selected.method,
            status="configured",
            reason=selected.detail or selected.source or f"{selected.method}_configured",
            configured_methods=configured_methods,
            available_methods=available_methods,
            fallback_methods=fallback_methods,
            runtime_confirmed=False,
            target_form_reached=selected.method in _RUNTIME_METHODS,
            billing_basis=selected.billing_basis,
            usage_tracking=selected.usage_tracking,
            quota_visibility=selected.quota_visibility,
            limitations=list(selected.limitations),
            project_verification=selected.project_verification,
            options=options,
        )

    reason = disabled_reasons[0] if disabled_reasons else "no_supported_access_method"
    return ProviderAccessDecision(
        provider=provider,
        selected_method=ACCESS_DISABLED,
        status="disabled",
        reason=reason,
        configured_methods=configured_methods,
        available_methods=available_methods,
        fallback_methods=fallback_methods,
        runtime_confirmed=False,
        target_form_reached=False,
        billing_basis=descriptive_option.billing_basis if descriptive_option else "",
        usage_tracking=descriptive_option.usage_tracking if descriptive_option else "",
        quota_visibility=descriptive_option.quota_visibility if descriptive_option else "",
        limitations=list(descriptive_option.limitations) if descriptive_option else [],
        project_verification=descriptive_option.project_verification if descriptive_option else "",
        options=options,
    )


def _select_descriptive_option(options: list[ProviderAccessOption], preferred_order: list[str]) -> ProviderAccessOption | None:
    method_rank = {method: index for index, method in enumerate(preferred_order)}
    candidates = [option for option in options if option.configured or option.enabled or option.official_support]
    if not candidates:
        return None
    return sorted(candidates, key=lambda option: (method_rank.get(option.method, 999), option.priority, option.method))[0]

def _load_connection_hints(path: Path) -> _ConnectionHints:
    hints = _ConnectionHints()
    if not path.exists():
        return hints
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return hints

    auth_profiles = ((cfg.get("auth") or {}).get("profiles") or {})
    if isinstance(auth_profiles, dict):
        for name, value in auth_profiles.items():
            provider = _infer_provider_from_blob(name, value)
            if provider and provider not in hints.oauth_profiles:
                hints.oauth_profiles[provider] = str(name)

    connectors = cfg.get("connectors") or {}
    if isinstance(connectors, dict):
        for name, value in connectors.items():
            provider = _infer_provider_from_blob(name, value)
            if not provider:
                continue
            connector_type = str((value or {}).get("type", "workspace") if isinstance(value, dict) else "workspace").lower()
            if "enterprise" in connector_type:
                hints.enterprise_connectors.setdefault(provider, str(name))
            else:
                hints.workspace_connectors.setdefault(provider, str(name))

    return hints


def _infer_provider_from_blob(name: str, value) -> str:
    candidates: list[str] = []
    if isinstance(value, dict):
        candidates.extend([
            str(value.get("provider") or ""),
            str(value.get("vendor") or ""),
            str(value.get("service") or ""),
            str(value.get("id") or ""),
        ])
    candidates.append(str(name or ""))
    normalized = " ".join(candidates).lower()
    for provider in sorted(_ALL_KNOWN_PROVIDERS, key=len, reverse=True):
        if provider in normalized:
            return provider
    return ""



