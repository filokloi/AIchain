#!/usr/bin/env python3
"""
aichaind.routing.ensemble — deterministic ensemble planner (roadmap #9).

For hard requests the router can do better than one model: best-of-N with a
judge, decomposition, or generate+verify (DYNAMIC_AUTO §"Ensembles"). Per
PROJECT_STATE §3 an ensemble runs ONLY for hard tasks and ONLY after the
user sees the cost estimate and confirms — so this module *plans*; it never
executes. The plan rides on the route decision as `ensemble_proposal` and
the harness decides.

Patterns by task type:
  - generate_verify   coding, math_logic, structured_output-ish work:
                      top model generates, a strong different model verifies.
  - best_of_n_judge   open-ended text (creative, chat, knowledge):
                      N cheap-but-good candidates, best model judges.
  - decompose         long_context, legal_formal, extraction:
                      cheap worker maps chunks, top model reduces.

MIT License.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DIFFICULTY_THRESHOLD = 80.0   # only genuinely hard requests
MIN_CHAIN_FOR_ENSEMBLE = 2    # need at least two distinct viable paths

_PATTERN_BY_TASK = {
    "coding": "generate_verify",
    "math_logic": "generate_verify",
    "agentic_tool_use": "generate_verify",
    "creative_writing": "best_of_n_judge",
    "chat_casual": "best_of_n_judge",
    "knowledge": "best_of_n_judge",
    "translation_language": "best_of_n_judge",
    "legal_formal": "decompose",
    "long_context": "decompose",
    "extraction": "decompose",
    "vision_reasoning": "best_of_n_judge",
    "vision_ocr": "decompose",
    # catalog-dimension fallbacks (pre-taxonomy catalogs)
    "reasoning": "generate_verify",
    "general_chat": "best_of_n_judge",
    "structured_output": "generate_verify",
    "tool_agent_compatibility": "generate_verify",
    "vision": "best_of_n_judge",
}


@dataclass
class EnsembleCall:
    model_id: str
    role: str          # candidate | judge | verifier | worker | synthesizer
    est_cost: float


@dataclass
class EnsemblePlan:
    pattern: str
    calls: list[EnsembleCall] = field(default_factory=list)
    est_total_cost: float = 0.0
    rationale: str = ""
    requires_confirmation: bool = True   # ALWAYS — never auto-execute

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "calls": [{"model": c.model_id, "role": c.role,
                       "est_cost_usd": round(c.est_cost, 6)} for c in self.calls],
            "est_total_cost_usd": round(self.est_total_cost, 6),
            "rationale": self.rationale,
            "requires_confirmation": self.requires_confirmation,
        }


def plan_ensemble(chain, task_type: str, difficulty: float,
                  budget_headroom: float = float("inf")):
    """chain: ranked list of pom.ScoredPath. Returns EnsemblePlan or None."""
    if difficulty < DIFFICULTY_THRESHOLD:
        return None
    distinct = []
    seen = set()
    for s in chain:
        if s.path.model.model_id not in seen:
            distinct.append(s)
            seen.add(s.path.model.model_id)
    if len(distinct) < MIN_CHAIN_FOR_ENSEMBLE:
        return None

    pattern = _PATTERN_BY_TASK.get(task_type, "best_of_n_judge")
    top = distinct[0]
    calls: list[EnsembleCall] = []

    if pattern == "generate_verify":
        verifier = distinct[1]
        calls = [EnsembleCall(top.path.model.model_id, "candidate", top.effective_cost),
                 EnsembleCall(verifier.path.model.model_id, "verifier",
                              verifier.effective_cost * 0.6)]
        rationale = "hard verifiable task: strongest model generates, an independent model checks"
    elif pattern == "decompose":
        worker = min(distinct[1:], key=lambda s: s.effective_cost)
        calls = [EnsembleCall(worker.path.model.model_id, "worker", worker.effective_cost),
                 EnsembleCall(worker.path.model.model_id, "worker", worker.effective_cost),
                 EnsembleCall(top.path.model.model_id, "synthesizer", top.effective_cost)]
        rationale = "long/formal input: cheap model maps sections, strongest model synthesizes"
    else:  # best_of_n_judge
        candidates = distinct[: min(3, len(distinct))]
        calls = [EnsembleCall(s.path.model.model_id, "candidate", s.effective_cost)
                 for s in candidates]
        calls.append(EnsembleCall(top.path.model.model_id, "judge",
                                  top.effective_cost * 0.4))
        rationale = "open-ended hard task: several candidates, strongest model judges"

    total = sum(c.est_cost for c in calls)
    if total > budget_headroom:
        return None
    return EnsemblePlan(pattern=pattern, calls=calls, est_total_cost=total,
                        rationale=rationale)
