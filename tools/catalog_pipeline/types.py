from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceHealth:
    name: str
    status: str = "unknown"
    healthy: bool = False
    fetched_records: int = 0
    accepted_records: int = 0
    coverage: float = 0.0
    latency_ms: int | None = None
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    snapshot_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "healthy": self.healthy,
            "fetched_records": self.fetched_records,
            "accepted_records": self.accepted_records,
            "coverage": round(self.coverage, 4),
            "latency_ms": self.latency_ms,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "snapshot_path": self.snapshot_path,
        }


@dataclass
class SourceResult:
    name: str
    records: list[dict[str, Any]]
    health: SourceHealth
    raw_payload: Any = None
    fetched_at: str = ""


@dataclass
class HelperCallResult:
    provider: str
    ok: bool
    payload: dict[str, Any] | list[Any] | None = None
    error: str | None = None


@dataclass
class HelperProviderStatus:
    name: str
    role: str
    configured: bool = False
    available: bool = False
    runtime_confirmed: bool = False
    status: str = "unknown"
    invocation_count: int = 0
    successes: int = 0
    failures: int = 0
    blocked_reason: str | None = None
    last_error: str | None = None
    used_as_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "configured": self.configured,
            "available": self.available,
            "runtime_confirmed": self.runtime_confirmed,
            "status": self.status,
            "invocation_count": self.invocation_count,
            "successes": self.successes,
            "failures": self.failures,
            "blocked_reason": self.blocked_reason,
            "last_error": self.last_error,
            "used_as_fallback": self.used_as_fallback,
        }


@dataclass
class HelperResolutionReport:
    available_helpers: list[str] = field(default_factory=list)
    used_provider: str | None = None
    fallback_used: bool = False
    failed_helpers: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    provider_statuses: dict[str, HelperProviderStatus] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available_helpers": list(self.available_helpers),
            "used_provider": self.used_provider,
            "fallback_used": self.fallback_used,
            "failed_helpers": list(self.failed_helpers),
            "events": list(self.events),
            "provider_statuses": {
                name: status.to_dict() for name, status in self.provider_statuses.items()
            },
        }


@dataclass
class MergeDiagnostics:
    deduped_catalog_duplicates: int = 0
    unresolved_aliases: list[str] = field(default_factory=list)
    helper_alias_resolutions: dict[str, str] = field(default_factory=dict)
    helper_task_enrichments: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deduped_catalog_duplicates": self.deduped_catalog_duplicates,
            "unresolved_aliases": list(self.unresolved_aliases),
            "helper_alias_resolutions": dict(self.helper_alias_resolutions),
            "helper_task_enrichments": {k: list(v) for k, v in self.helper_task_enrichments.items()},
        }
