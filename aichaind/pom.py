"""AIchain — Personal Opportunity Matrix (POM) and value-density routing core.

Deterministic decision core for aichaind. No network, no LLM calls: pure
functions over (global catalog, user truth, live state) -> ranked chain.
Spec: docs/ROUTING_POLICY.md and docs/DYNAMIC_AUTO.md (§2b, lock semantics).

MIT License.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

EPSILON = 0.5          # value-density denominator guard for free paths
BASE_SWITCH_THRESHOLD = 15.0


class PathType(str, Enum):
    LOCAL = "local"
    FREE_QUOTA = "free_quota"
    PREPAID_CREDIT = "prepaid_credit"
    SUBSCRIPTION_API = "subscription_api"
    PAY_AS_YOU_GO = "pay_as_you_go"


class Boundary(str, Enum):
    ANY = "any"
    NO_TRAINING_DATA = "no_training_data"
    EU_HOSTED_ONLY = "eu_hosted_only"
    LOCAL_ONLY = "local_only"


@dataclass(frozen=True)
class CatalogModel:
    """Global truth: one model as published in catalog_manifest.json."""
    model_id: str
    provider: str
    task_scores: dict[str, float]          # task_type -> 0..100
    price_in: float                        # $/M input tokens (list price)
    price_out: float                       # $/M output tokens
    context_window: int
    supports_tools: bool = False
    supports_vision: bool = False
    cached_rate_factor: float = 0.1        # cached prefix price multiplier
    no_training_data: bool = False
    eu_hosted: bool = False


@dataclass
class AccessPath:
    """One way THIS user can reach a model. Same model may have several."""
    model: CatalogModel
    path_type: PathType
    quota_remaining: float = 0.0           # requests, for FREE_QUOTA
    credit_remaining: float = 0.0          # $, for PREPAID_CREDIT
    credit_days_to_expiry: Optional[int] = None
    endpoint: Optional[str] = None         # for LOCAL
    measured_speed_score: Optional[float] = None  # local probe override 0..100


@dataclass
class Request:
    task_type: str
    difficulty: float                      # 0..100 estimate
    est_input_tokens: int
    est_output_tokens: int
    needs_tools: bool = False
    needs_vision: bool = False
    boundary: Boundary = Boundary.ANY
    transcript_tokens: int = 0             # history size (cache economics)
    sticky_model_id: Optional[str] = None
    locked_model_id: Optional[str] = None  # hard lock (DYNAMIC_AUTO)


@dataclass
class Profile:
    intelligence: float = 50.0             # slider 0..100
    cost_sensitivity: float = 50.0         # slider 0..100
    min_intelligence: float = 0.0

    @property
    def gamma(self) -> float:
        """Quality exponent: 1.0 (linear) .. 3.0 (quality dominates)."""
        return 1.0 + 2.0 * (self.intelligence / 100.0)


@dataclass
class Budget:
    daily_limit: float = float("inf")
    spent_today: float = 0.0
    soft_threshold: float = 0.8
    hard_stop: bool = True

    @property
    def headroom(self) -> float:
        return max(0.0, self.daily_limit - self.spent_today)

    @property
    def in_soft_zone(self) -> bool:
        if self.daily_limit == float("inf"):
            return False
        return self.spent_today >= self.soft_threshold * self.daily_limit


@dataclass
class ScoredPath:
    path: AccessPath
    effective_cost: float
    value_density: float
    excluded_reason: Optional[str] = None


# ---------------------------------------------------------------- effective cost

def effective_cost(path: AccessPath, req: Request, sticky: bool = False) -> float:
    """Marginal $ cost of answering `req` via `path`, cache-aware.

    LOCAL and FREE_QUOTA (with quota) are 0.  SUBSCRIPTION_API is 0 marginal.
    Paid paths: blended list price over estimated tokens; if `sticky` and the
    provider caches the prefix, the transcript is billed at the cached rate,
    otherwise the full transcript re-bills at full input price (this is what
    makes switching mid-conversation genuinely expensive).
    """
    if path.path_type == PathType.LOCAL:
        return 0.0
    if path.path_type == PathType.FREE_QUOTA:
        return 0.0 if path.quota_remaining >= 1 else float("inf")
    if path.path_type == PathType.SUBSCRIPTION_API:
        return 0.0

    m = path.model
    prefix = req.transcript_tokens
    prefix_rate = m.price_in * (m.cached_rate_factor if sticky else 1.0)
    cost = (
        prefix / 1e6 * prefix_rate
        + req.est_input_tokens / 1e6 * m.price_in
        + req.est_output_tokens / 1e6 * m.price_out
    )
    if path.path_type == PathType.PREPAID_CREDIT and cost > path.credit_remaining:
        return float("inf")  # this path cannot cover the request
    return cost


def urgency_bonus(path: AccessPath, horizon_days: int = 7) -> float:
    """Use-it-or-lose-it multiplier for expiring prepaid value."""
    if (
        path.path_type == PathType.PREPAID_CREDIT
        and path.credit_days_to_expiry is not None
        and path.credit_days_to_expiry <= horizon_days
    ):
        return 1.0 + (horizon_days - path.credit_days_to_expiry) / horizon_days
    return 1.0


# ---------------------------------------------------------------- hard filters

def hard_filter(path: AccessPath, req: Request, profile: Profile) -> Optional[str]:
    """Return exclusion reason, or None if the path survives.

    Order matters and mirrors the spec: privacy first (nothing overrides it),
    then capabilities, then context fit, then the dynamic intelligence floor.
    """
    m = path.model
    b = req.boundary
    if b == Boundary.LOCAL_ONLY and path.path_type != PathType.LOCAL:
        return "privacy:local_only"
    if b == Boundary.NO_TRAINING_DATA and not (
        m.no_training_data or path.path_type == PathType.LOCAL
    ):
        return "privacy:no_training_data"
    if b == Boundary.EU_HOSTED_ONLY and not (
        m.eu_hosted or path.path_type == PathType.LOCAL
    ):
        return "privacy:eu_hosted_only"

    if req.needs_tools and not m.supports_tools:
        return "capability:tools"
    if req.needs_vision and not m.supports_vision:
        return "capability:vision"

    total = req.transcript_tokens + req.est_input_tokens + req.est_output_tokens
    if total > m.context_window:
        return "capability:context_window"

    floor = max(profile.min_intelligence, req.difficulty * 0.8)
    if m.task_scores.get(req.task_type, 0.0) < floor:
        return f"floor:{floor:.0f}"
    return None


# ---------------------------------------------------------------- scoring

def score_path(path: AccessPath, req: Request, profile: Profile,
               budget: Budget) -> ScoredPath:
    sticky = req.sticky_model_id == path.model.model_id
    reason = hard_filter(path, req, profile)
    if reason:
        return ScoredPath(path, float("inf"), 0.0, reason)

    cost = effective_cost(path, req, sticky=sticky)
    if cost == float("inf"):
        return ScoredPath(path, cost, 0.0, "path:exhausted")
    if cost > 0 and budget.hard_stop and cost > budget.headroom:
        return ScoredPath(path, cost, 0.0, "budget:hard_stop")

    q = path.model.task_scores.get(req.task_type, 0.0)
    # Cost pressure scales with the slider: at cost_sensitivity=0 the cost
    # term vanishes (quality decides; budget guard still protects), at 100
    # it bites fully. Soft budget zone doubles the pressure regardless.
    cost_pressure = profile.cost_sensitivity / 100.0
    if budget.in_soft_zone:
        cost_pressure = max(cost_pressure * 2.0, 1.0)

    vd = (q ** profile.gamma) / ((cost * cost_pressure * 100.0) + EPSILON)
    vd *= urgency_bonus(path)
    return ScoredPath(path, cost, vd)


def switch_threshold(transcript_tokens: int) -> float:
    """Score advantage a challenger needs to unseat the sticky model.

    Grows with transcript length because switching discards the provider-side
    prefix cache (OPTIMIZATIONS.md §4): +1 point per 10k tokens of history.
    """
    return BASE_SWITCH_THRESHOLD + transcript_tokens / 10_000.0


def build_chain(paths: list[AccessPath], req: Request, profile: Profile,
                budget: Budget, max_depth: int = 4) -> list[ScoredPath]:
    """Full pipeline: lock -> score -> stickiness -> ranked chain."""
    # Hard lock: only the locked model's paths; privacy still applies.
    if req.locked_model_id:
        pool = [p for p in paths if p.model.model_id == req.locked_model_id]
        chain: list[ScoredPath] = []
        for p in pool:
            reason = hard_filter(p, req, Profile(min_intelligence=0.0))
            if reason and reason.startswith("privacy"):
                continue  # privacy is the one filter lock cannot override
            cost = effective_cost(p, req,
                                  sticky=req.sticky_model_id == p.model.model_id)
            if cost == float("inf"):
                continue
            chain.append(ScoredPath(p, cost, 1.0))
        chain.sort(key=lambda s: s.effective_cost)  # cheapest path to locked model
        return chain[:max_depth]

    scored = [score_path(p, req, profile, budget) for p in paths]
    alive = [s for s in scored if s.excluded_reason is None]
    alive.sort(key=lambda s: s.value_density, reverse=True)
    alive = alive[:max_depth]

    # Sticky promotion with length-scaled hysteresis.
    if req.sticky_model_id and alive:
        idx = next((i for i, s in enumerate(alive)
                    if s.path.model.model_id == req.sticky_model_id), None)
        if idx is not None and idx > 0:
            best, sticky_s = alive[0], alive[idx]
            rel_gap = (best.value_density - sticky_s.value_density) \
                / max(sticky_s.value_density, 1e-9) * 100.0
            if rel_gap < switch_threshold(req.transcript_tokens):
                alive.insert(0, alive.pop(idx))
    return alive
