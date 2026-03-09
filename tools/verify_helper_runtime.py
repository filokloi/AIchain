#!/usr/bin/env python3
"""Verify helper runtime using real configured credentials."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.catalog_pipeline.credentials import resolve_credential
from tools.catalog_pipeline.helper_ai.providers import GeminiHelperProvider, GroqHelperProvider
from tools.catalog_pipeline.helper_ai.service import AIHelperService


class ForcedFailureProvider:
    name = "forced_primary_failure"

    def __init__(self) -> None:
        self.api_key = "forced"

    @property
    def available(self) -> bool:
        return True

    def call_json(self, prompt: str):
        from tools.catalog_pipeline.types import HelperCallResult
        return HelperCallResult(provider=self.name, ok=False, error="forced primary failure for fallback verification")


@dataclass
class HelperRuntimeStatus:
    status: str
    gemini_configured: bool
    groq_configured: bool
    gemini_runtime_confirmed: bool
    groq_runtime_confirmed: bool
    fallback_confirmed: bool
    reasons: list[str]


def probe_helper_runtime() -> HelperRuntimeStatus:
    gemini_key = resolve_credential("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    groq_key = resolve_credential("GROQ_KEY", "GROQ_API_KEY")
    reasons: list[str] = []

    gemini_configured = bool(gemini_key)
    groq_configured = bool(groq_key)
    gemini_runtime_confirmed = False
    groq_runtime_confirmed = False
    fallback_confirmed = False

    prompt_models = [{"id": "google/gemini-2.5-flash", "context_length": 1000000, "task_primary": ["general_chat"]}]

    if gemini_configured:
        helper = AIHelperService(primary=GeminiHelperProvider(gemini_key), fallback=None)
        helper.prioritize_free_models(prompt_models)
        status = helper.to_dict().get("provider_statuses", {}).get("gemini", {})
        gemini_runtime_confirmed = bool(status.get("runtime_confirmed"))
        if not gemini_runtime_confirmed:
            reasons.append(f"gemini_not_confirmed:{status.get('last_error') or status.get('status')}")
    else:
        reasons.append("gemini_missing_credentials")

    if groq_configured:
        helper = AIHelperService(primary=ForcedFailureProvider(), fallback=GroqHelperProvider(groq_key))
        helper.prioritize_free_models(prompt_models)
        report = helper.to_dict()
        groq_status = report.get("provider_statuses", {}).get("groq", {})
        groq_runtime_confirmed = bool(groq_status.get("runtime_confirmed"))
        fallback_confirmed = bool(report.get("fallback_used")) and groq_runtime_confirmed
        if not groq_runtime_confirmed:
            reasons.append(f"groq_not_confirmed:{groq_status.get('last_error') or groq_status.get('status')}")
        if not fallback_confirmed:
            reasons.append("groq_fallback_not_confirmed")
    else:
        reasons.append("groq_missing_credentials")

    if fallback_confirmed:
        status = "runtime_confirmed"
    elif groq_configured or gemini_configured:
        status = "partially_confirmed"
    else:
        status = "blocked_missing_credentials"

    return HelperRuntimeStatus(
        status=status,
        gemini_configured=gemini_configured,
        groq_configured=groq_configured,
        gemini_runtime_confirmed=gemini_runtime_confirmed,
        groq_runtime_confirmed=groq_runtime_confirmed,
        fallback_confirmed=fallback_confirmed,
        reasons=reasons,
    )


def main() -> int:
    result = probe_helper_runtime()
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    return 0 if result.status == "runtime_confirmed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
