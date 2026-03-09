from __future__ import annotations

import math
from typing import Any

from ..constants import SUPPORTED_TASK_TYPES


_REASONING_TOKENS = ("reason", "thinking", "o1", "o3", "r1", "deep-research", "qwq")
_CODING_TOKENS = ("codex", "coder", "code", "dev", "sonnet", "4.1", "o4-mini")
_VISION_TOKENS = ("vision", "-vl", "/vl", "gpt-4o", "gemini", "pixtral")
_STRUCTURED_TOKENS = ("openai/", "google/", "anthropic/", "codex", "json")
_TOOL_TOKENS = ("openai/", "anthropic/", "google/", "codex", "sonnet")


def _cap(score: float) -> int:
    return max(0, min(100, int(round(score))))


def infer_task_metadata(
    *,
    model_id: str,
    provider: str,
    context_length: int,
    intelligence: int,
    helper_tasks: list[str] | None = None,
) -> dict[str, Any]:
    model_lower = model_id.lower()
    context_score = _cap(35 + 18 * math.log2(max(context_length / 8192, 1)))
    reasoning = intelligence + (12 if any(token in model_lower for token in _REASONING_TOKENS) else 0)
    coding = intelligence - 4 + (16 if any(token in model_lower for token in _CODING_TOKENS) else 0)
    vision = 18 + (58 if any(token in model_lower for token in _VISION_TOKENS) else 0)
    long_context = max(context_score, 20)
    extraction = intelligence * 0.72 + context_score * 0.25
    structured_output = intelligence * 0.68 + (18 if any(token in model_lower for token in _STRUCTURED_TOKENS) else 0)
    general_chat = intelligence * 0.88 + 6
    tool_agent = intelligence * 0.58 + context_score * 0.18 + (20 if any(token in model_lower for token in _TOOL_TOKENS) else 0)

    quality_by_task = {
        "coding": _cap(coding),
        "reasoning": _cap(reasoning),
        "vision": _cap(vision),
        "long_context": _cap(long_context),
        "extraction": _cap(extraction),
        "structured_output": _cap(structured_output),
        "general_chat": _cap(general_chat),
        "tool_agent_compatibility": _cap(tool_agent),
    }

    confidence = 0.68
    helper_tasks = [task for task in (helper_tasks or []) if task in SUPPORTED_TASK_TYPES]
    if helper_tasks:
        confidence = 0.86
        for task in helper_tasks:
            quality_by_task[task] = min(100, quality_by_task.get(task, 60) + 12)

    supported = sorted(task for task, score in quality_by_task.items() if score >= 70)
    primary = [task for task, _score in sorted(quality_by_task.items(), key=lambda item: (-item[1], item[0]))[:3]]
    coverage_score = round(sum(quality_by_task.values()) / len(quality_by_task), 2)

    return {
        "quality_by_task": quality_by_task,
        "supported": supported,
        "primary": primary,
        "coverage_score": coverage_score,
        "confidence": confidence,
        "helper_enriched": bool(helper_tasks),
        "provider_family": provider,
    }
