#!/usr/bin/env python3
"""
aichaind.routing.pom_bridge — bridge between the live router and pom.py.

Converts (routing table entries, user_truth assets) into pom.AccessPath
objects and exposes route() -> ranked chain via pom.build_chain().

Deterministic, no network. The cascade router calls this AFTER the task
preference is known; an empty chain means "POM has no basis to decide"
and the legacy cost optimizer takes over (graceful degradation).

MIT License.
"""
from __future__ import annotations

import logging
from typing import Optional

from aichaind.pom import (AccessPath, Boundary, Budget, CatalogModel,
                          PathType, Profile, Request, ScoredPath, build_chain)
from aichaind.routing.task_classifier import classify
from aichaind.user_truth import (boundary_from_truth, budget_from_truth,
                                 profile_from_truth)

log = logging.getLogger("aichaind.routing.pom_bridge")

#: cascade/task_hint vocabulary -> catalog quality_by_task keys
_TASK_TYPE_MAP = {
    "code": "coding", "coding": "coding", "programming": "coding",
    "reason": "reasoning", "reasoning": "reasoning", "analysis": "reasoning",
    "vision": "vision", "visual": "vision", "image": "vision",
    "long_context": "long_context", "summar": "long_context",
    "extract": "extraction", "extraction": "extraction",
    "structured": "structured_output", "json": "structured_output",
    "tool": "tool_agent_compatibility", "agent": "tool_agent_compatibility",
    "chat": "general_chat", "general": "general_chat",
}
DEFAULT_TASK_TYPE = "general_chat"

#: preference tier -> difficulty estimate when nothing better is known
_PREFERENCE_DIFFICULTY = {"free": 35.0, "local": 30.0, "heavy": 75.0, "visual": 60.0}


def map_task_type(task_hint: str, model_preference: str = "") -> str:
    hint = (task_hint or "").lower()
    for token, task_type in _TASK_TYPE_MAP.items():
        if token in hint:
            return task_type
    if model_preference == "visual":
        return "vision"
    if model_preference == "heavy":
        return "reasoning"
    return DEFAULT_TASK_TYPE


def catalog_model_from_entry(entry: dict) -> Optional[CatalogModel]:
    """One routing_hierarchy entry -> CatalogModel. Returns None if unusable."""
    model_id = entry.get("model") or entry.get("family_id")
    if not model_id:
        return None
    raw = entry.get("raw_metrics", {}) or {}
    tm = entry.get("task_metadata", {}) or {}
    scores = dict(tm.get("quality_by_task", {}) or {})
    if not scores:
        intelligence = float(entry.get("normalized_metrics", {}).get("intelligence", 0.0) or 0.0)
        scores = {DEFAULT_TASK_TYPE: intelligence}
    supported = set(tm.get("supported", []) or [])
    return CatalogModel(
        model_id=model_id,
        provider=str(entry.get("provider", "")).lower(),
        task_scores=scores,
        price_in=float(raw.get("prompt_cost", 0.0) or 0.0) * 1e6,
        price_out=float(raw.get("completion_cost", 0.0) or 0.0) * 1e6,
        context_window=int(raw.get("context_length", 0) or 0),
        supports_tools="tool_agent_compatibility" in supported,
        supports_vision="vision" in supported or scores.get("vision", 0.0) >= 50.0,
    )


class PomRouter:
    """Holds the catalog x user_truth cross product and answers route()."""

    def __init__(self, routing_table: dict | None, user_truth: dict,
                 spent_today_fn=None):
        self._truth = user_truth or {}
        self._spent_today_fn = spent_today_fn or (lambda: 0.0)
        self._profile = profile_from_truth(self._truth)
        self._catalog: dict[str, CatalogModel] = {}
        self._by_provider: dict[str, list[CatalogModel]] = {}
        for entry in (routing_table or {}).get("routing_hierarchy", []) or []:
            cm = catalog_model_from_entry(entry)
            if cm is None:
                continue
            self._catalog[cm.model_id] = cm
            self._by_provider.setdefault(cm.provider, []).append(cm)
        self._paths = self._build_paths()
        log.info(f"PomRouter: {len(self._catalog)} catalog models, "
                 f"{len(self._paths)} access paths from user_truth assets")

    # ---------------------------------------------------------------- paths

    def _lookup(self, provider: str, model_id: str | None = None) -> list[CatalogModel]:
        if model_id:
            cm = self._catalog.get(model_id)
            return [cm] if cm else []
        return self._by_provider.get(provider.lower(), [])

    def _build_paths(self) -> list[AccessPath]:
        assets = self._truth.get("assets", {}) or {}
        paths: list[AccessPath] = []

        for lm in assets.get("local_models", []) or []:
            ref = lm.get("catalog_ref") or lm.get("model_id")
            cm = self._catalog.get(ref)
            if cm is None:
                # Local model absent from the global catalog: synthesize a
                # conservative stub so the path still exists.
                cm = CatalogModel(
                    model_id=lm.get("model_id", ref or "local/unknown"),
                    provider="local", task_scores={DEFAULT_TASK_TYPE: 55.0},
                    price_in=0.0, price_out=0.0, context_window=32_000,
                )
            paths.append(AccessPath(model=cm, path_type=PathType.LOCAL,
                                    endpoint=lm.get("endpoint")))

        for fq in assets.get("free_quotas", []) or []:
            for cm in self._lookup(fq.get("provider", "")):
                paths.append(AccessPath(model=cm, path_type=PathType.FREE_QUOTA,
                                        quota_remaining=float(fq.get("quota_per_day", 0.0))))

        for sub in assets.get("subscriptions", []) or []:
            # "Legitimate free, not gray free": app-only plans are never
            # exposed as programmatic paths (PROJECT_STATE §3).
            if sub.get("access_type") != "official_api_included":
                continue
            for cm in self._lookup(sub.get("provider", "")):
                paths.append(AccessPath(model=cm, path_type=PathType.SUBSCRIPTION_API))

        for key in assets.get("api_keys", []) or []:
            credit = float(key.get("prepaid_credit", 0.0) or 0.0)
            days = None
            if key.get("credit_expires"):
                days = _days_until(key["credit_expires"])
            for cm in self._lookup(key.get("provider", "")):
                if credit > 0:
                    paths.append(AccessPath(model=cm, path_type=PathType.PREPAID_CREDIT,
                                            credit_remaining=credit,
                                            credit_days_to_expiry=days))
                else:
                    paths.append(AccessPath(model=cm, path_type=PathType.PAY_AS_YOU_GO))
        return paths

    # ---------------------------------------------------------------- route

    @property
    def enabled(self) -> bool:
        return bool(self._paths)

    def route(self, *, task_hint: str = "", model_preference: str = "",
              messages: list[dict] | None = None,
              est_input_tokens: int = 1000, est_output_tokens: int = 500,
              transcript_tokens: int = 0,
              needs_tools: bool = False, needs_vision: bool = False,
              tool_schema_present: bool = False,
              attachment_types: list[str] | None = None,
              sticky_model_id: str | None = None,
              locked_model_id: str | None = None,
              max_depth: int = 4) -> list[ScoredPath]:
        """Rank this user's access paths for one request via pom.build_chain.

        Task type and difficulty come from the tier-1 deterministic
        classifier (DYNAMIC_AUTO §2); task_hint/model_preference act as the
        tier-3 harness hint when the classifier is not confident.
        """
        if not self._paths:
            return []
        text = " ".join(str(m.get("content", "")) for m in (messages or [])
                        if isinstance(m.get("content"), str))
        cls = classify(messages, tool_schema_present=tool_schema_present,
                       attachment_types=attachment_types,
                       transcript_tokens=transcript_tokens)
        if cls.confidence >= 0.6:
            task_type = cls.catalog_dimension
            difficulty = cls.difficulty
        else:
            task_type = map_task_type(task_hint, model_preference)
            difficulty = max(cls.difficulty,
                             _PREFERENCE_DIFFICULTY.get(model_preference, 50.0))
        req = Request(
            task_type=task_type,
            difficulty=difficulty,
            est_input_tokens=est_input_tokens,
            est_output_tokens=est_output_tokens,
            needs_tools=needs_tools or cls.task_type == "agentic_tool_use",
            needs_vision=needs_vision or task_type == "vision",
            boundary=boundary_from_truth(self._truth, text=text),
            transcript_tokens=transcript_tokens,
            sticky_model_id=sticky_model_id,
            locked_model_id=locked_model_id,
        )
        budget = budget_from_truth(self._truth, spent_today=self._spent_today_fn())
        chain = build_chain(self._paths, req, self._profile, budget, max_depth=max_depth)
        if chain:
            top = chain[0]
            log.debug(f"POM chain: {[s.path.model.model_id for s in chain]} "
                      f"(top vd={top.value_density:.2f}, cost=${top.effective_cost:.6f})")
        return chain


def _days_until(iso_date: str) -> Optional[int]:
    from datetime import date
    try:
        y, m, d = (int(x) for x in iso_date[:10].split("-"))
        return (date(y, m, d) - date.today()).days
    except (ValueError, AttributeError):
        return None
