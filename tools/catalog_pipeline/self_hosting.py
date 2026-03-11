from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_SELF_HOSTABLE_FAMILIES = (
    {
        "family": "Qwen",
        "patterns": ("qwen/qwen2.5-", "qwen/qwen3-", "qwen/qwq-"),
        "download_sources": ("huggingface", "ollama", "lmstudio"),
        "preferred_runtimes": ("ollama", "lmstudio", "vllm"),
        "notes": "Open-weight Qwen family. Practical viability depends on quantization choice and local memory budget.",
    },
    {
        "family": "DeepSeek",
        "patterns": ("deepseek/deepseek-r1", "deepseek/deepseek-r1-distill-", "deepseek/deepseek-v3"),
        "download_sources": ("huggingface", "ollama", "lmstudio"),
        "preferred_runtimes": ("ollama", "lmstudio", "vllm"),
        "notes": "DeepSeek open-weight reasoning family. Large variants often require aggressive quantization or stronger hardware.",
    },
    {
        "family": "Gemma",
        "patterns": ("google/gemma-",),
        "download_sources": ("huggingface", "ollama", "lmstudio"),
        "preferred_runtimes": ("ollama", "lmstudio", "llamacpp"),
        "notes": "Gemma family is well suited for compact self-hosted deployments and consumer hardware tiers.",
    },
    {
        "family": "Llama",
        "patterns": ("meta-llama/",),
        "download_sources": ("huggingface", "ollama", "lmstudio"),
        "preferred_runtimes": ("ollama", "lmstudio", "vllm"),
        "notes": "Meta Llama family has a mature self-hosting ecosystem and broad quantization support.",
    },
    {
        "family": "Nemotron",
        "patterns": ("nvidia/nemotron-", "nvidia/llama-3.1-nemotron-"),
        "download_sources": ("huggingface",),
        "preferred_runtimes": ("vllm", "tensorrt-llm"),
        "notes": "Nemotron class models are generally self-hostable on workstation or server-class hardware, not consumer laptops.",
    },
)


def _match_family(model_id: str) -> dict[str, Any] | None:
    lower = str(model_id or "").lower()
    for family in _SELF_HOSTABLE_FAMILIES:
        if any(lower.startswith(pattern) for pattern in family["patterns"]):
            return family
    return None


def _parameter_scale_billions(model_id: str) -> float | None:
    normalized = str(model_id or "").lower().replace("-", " ")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([bm])\b", normalized)
    if not match:
        return None
    value = float(match.group(1))
    if match.group(2) == "m":
        return round(value / 1000.0, 3)
    return round(value, 3)


def _hardware_profile_hint(param_billions: float | None) -> str | None:
    if param_billions is None:
        return None
    if param_billions <= 2:
        return "edge_or_8gb_shared_memory"
    if param_billions <= 4:
        return "8gb_unified_or_12gb_vram_recommended"
    if param_billions <= 8:
        return "16gb_memory_class_recommended"
    if param_billions <= 14:
        return "24gb_memory_class_recommended"
    if param_billions <= 32:
        return "32gb_plus_recommended"
    if param_billions <= 72:
        return "48gb_plus_or_multi_gpu_recommended"
    return "server_class_80gb_plus_or_multi_gpu"


def _quantizations_known(param_billions: float | None) -> list[str]:
    if param_billions is None:
        return ["BF16", "Q4_K_M", "Q5_K_M"]
    if param_billions <= 4:
        return ["BF16", "Q4_K_M", "Q5_K_M", "Q8_0"]
    if param_billions <= 8:
        return ["BF16", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"]
    if param_billions <= 14:
        return ["BF16", "Q4_K_M", "Q5_K_M", "Q6_K"]
    if param_billions <= 32:
        return ["BF16", "Q3_K_M", "Q4_K_M", "Q5_K_M"]
    if param_billions <= 72:
        return ["BF16", "Q2_K", "Q3_K_M", "Q4_K_M"]
    return ["BF16", "Q2_K", "Q3_K_S", "Q3_K_M"]


def _benchmark_evidence_sources(source_attribution: dict[str, Any], raw_metrics: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    catalogs = source_attribution.get("catalog", []) if isinstance(source_attribution, dict) else []
    if any(source == "openrouter" for source in catalogs):
        evidence.append("openrouter_catalog")
    if source_attribution.get("intelligence") == "benchmark":
        evidence.append("curated_benchmark_map")
    if isinstance(raw_metrics.get("aa_quality"), (int, float)):
        evidence.append("artificial_analysis")
    if isinstance(raw_metrics.get("lmsys_elo"), (int, float)):
        evidence.append("lmsys_arena")
    return evidence


def derive_self_hosting_profile(
    model_id: str,
    *,
    source_attribution: dict[str, Any] | None = None,
    raw_metrics: dict[str, Any] | None = None,
    source_kind: str = "openrouter",
) -> dict[str, Any]:
    source_attribution = source_attribution or {}
    raw_metrics = raw_metrics or {}
    family = _match_family(model_id)
    param_billions = _parameter_scale_billions(model_id)
    evidence_sources = _benchmark_evidence_sources(source_attribution, raw_metrics)

    hosting_modes: list[str] = []
    if source_kind == "bridge":
        hosting_modes.append("oauth_bridge")
    else:
        hosting_modes.append("cloud_api")
    if family:
        hosting_modes.append("self_hosted")

    return {
        "self_hostable": bool(family),
        "open_weight": bool(family),
        "family": family["family"] if family else None,
        "hosting_modes": hosting_modes,
        "download_sources": list(family["download_sources"]) if family else [],
        "preferred_runtimes": list(family["preferred_runtimes"]) if family else [],
        "quantizations_known": _quantizations_known(param_billions) if family else [],
        "parameter_scale_billions": param_billions,
        "hardware_profile_hint": _hardware_profile_hint(param_billions) if family else None,
        "self_hosting_notes": family["notes"] if family else None,
        "benchmark_evidence_sources": evidence_sources,
    }


def build_self_hosted_model_index(entries: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    family_breakdown: dict[str, int] = {}

    for entry in entries:
        profile = entry.get("self_hosting") or {}
        if not profile.get("self_hostable"):
            continue
        family = profile.get("family") or "Unknown"
        family_breakdown[family] = family_breakdown.get(family, 0) + 1
        candidates.append(
            {
                "model": entry.get("model"),
                "display_name": entry.get("display_name") or entry.get("model"),
                "family_id": entry.get("family_id"),
                "family": family,
                "rank": entry.get("rank"),
                "provider": entry.get("provider"),
                "tier": entry.get("tier"),
                "task_label": entry.get("task_label"),
                "context_length": entry.get("raw_metrics", {}).get("context_length"),
                "metrics": deepcopy(entry.get("metrics", {})),
                "hosting_modes": list(profile.get("hosting_modes", [])),
                "download_sources": list(profile.get("download_sources", [])),
                "preferred_runtimes": list(profile.get("preferred_runtimes", [])),
                "quantizations_known": list(profile.get("quantizations_known", [])),
                "parameter_scale_billions": profile.get("parameter_scale_billions"),
                "hardware_profile_hint": profile.get("hardware_profile_hint"),
                "self_hosting_notes": profile.get("self_hosting_notes"),
                "benchmark_evidence_sources": list(profile.get("benchmark_evidence_sources", [])),
            }
        )

    candidates.sort(key=lambda item: (item.get("rank") or 9999, item.get("model") or ""))
    return {
        "total_models": len(candidates),
        "family_breakdown": dict(sorted(family_breakdown.items())),
        "entries": candidates,
    }
