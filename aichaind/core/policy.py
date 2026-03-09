#!/usr/bin/env python3
"""
aichaind.core.policy — Policy-as-Code Engine

YAML-driven routing policies for budget, privacy, and provider selection.
"""

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("aichaind.core.policy")


@dataclass
class PolicyResult:
    """Result of policy evaluation."""
    allowed: bool = True
    reason: str = ""
    force_model: str = ""          # If set, override routing to this model
    force_provider: str = ""       # If set, restrict to this provider
    block_cloud: bool = False      # Block all cloud providers
    max_cost_per_turn: float = 0.0 # 0 = no limit


class PolicyEngine:
    """
    Evaluates routing policies.

    Policies are loaded from config (v1: dict-based, v1.1: YAML).
    Example rules:
    - If PII detected → block_cloud
    - If budget > 80% → force free-tier
    - If model blacklisted → deny
    """

    def __init__(self, policy_config: dict = None):
        self.rules = policy_config or {}
        self._model_blacklist: set = set(self.rules.get("model_blacklist", []))
        self._provider_blacklist: set = set(self.rules.get("provider_blacklist", []))
        self._max_cost_per_turn: float = self.rules.get("max_cost_per_turn", 1.0)
        self._max_cost_per_session: float = self.rules.get("max_cost_per_session", 10.0)
        self._pii_blocks_cloud: bool = self.rules.get("pii_blocks_cloud", True)

    def evaluate(
        self,
        target_model: str = "",
        target_provider: str = "",
        contains_pii: bool = False,
        budget_spent: float = 0.0,
        budget_limit: float = 10.0,
    ) -> PolicyResult:
        """Evaluate all policies against a routing candidate."""

        # Model blacklist
        if target_model in self._model_blacklist:
            return PolicyResult(allowed=False, reason=f"model_blacklisted: {target_model}")

        # Provider blacklist
        if target_provider in self._provider_blacklist:
            return PolicyResult(allowed=False, reason=f"provider_blacklisted: {target_provider}")

        # PII → block cloud
        if contains_pii and self._pii_blocks_cloud:
            return PolicyResult(
                allowed=True,
                block_cloud=True,
                reason="pii_detected_cloud_blocked",
            )

        # Budget enforcement
        if budget_spent >= budget_limit:
            return PolicyResult(
                allowed=True,
                reason="budget_exhausted_free_only",
                max_cost_per_turn=0.0,
            )
        if budget_spent >= budget_limit * 0.8:
            return PolicyResult(
                allowed=True,
                reason="budget_warning",
                max_cost_per_turn=self._max_cost_per_turn * 0.5,
            )

        return PolicyResult(
            allowed=True,
            max_cost_per_turn=self._max_cost_per_turn,
        )
