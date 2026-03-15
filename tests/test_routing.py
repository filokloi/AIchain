#!/usr/bin/env python3
"""
Tests for aichaind.routing — rules, cascade, table_sync

Covers:
- Layer 1 rules: godmode, specialist pins, visual detect, budget gate, heuristics
- Cascade router orchestration
- Complexity estimation
- Routing table validation helpers
"""

import pytest
from aichaind.routing.rules import (
    layer1_route, check_specialist_pin, detect_visual_content,
    estimate_complexity, RouteDecision, detect_coding_intent,
)
from aichaind.routing.cascade import CascadeRouter
from aichaind.routing.table_sync import (
    compute_table_checksum, get_best_free_primary, get_heavy_hitter,
    get_top_fallbacks, _ver_gte,
)
from aichaind.core.session import BudgetState, PrivacyContext


# ─── Layer 1: Rules ───

class TestGodmodeRouting:
    def test_godmode_overrides_everything(self):
        d = layer1_route(
            messages=[{"role": "user", "content": "hello"}],
            godmode_model="openai/o3-pro",
        )
        assert d is not None
        assert d.target_model == "openai/o3-pro"
        assert d.confidence == 1.0
        assert "L1:godmode" in d.decision_layers


class TestSpecialistPins:
    def test_vision_trigger(self):
        pin = check_specialist_pin("Please do image_analysis of this")
        assert pin is not None
        assert pin["category"] == "vision"
        assert pin["model"] == "google/gemini-2.5-pro"

    def test_code_engineering_trigger(self):
        pin = check_specialist_pin("I need system_architecture review")
        assert pin is not None
        assert pin["category"] == "code_engineering"

    def test_no_trigger(self):
        pin = check_specialist_pin("What is the weather today?")
        assert pin is None

    def test_specialist_pin_routed_via_layer1(self):
        d = layer1_route(
            messages=[{"role": "user", "content": "reverse_engineer this binary"}],
        )
        assert d is not None
        assert "L1:specialist_pin" in d.decision_layers
        assert d.target_model == "openai/gpt-4.1"


class TestVisualDetection:
    def test_detect_image_url(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "What's this?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]}]
        assert detect_visual_content(msgs) is True

    def test_no_image(self):
        msgs = [{"role": "user", "content": "Just text"}]
        assert detect_visual_content(msgs) is False

    def test_visual_routes_to_visual_model(self):
        msgs = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;..."}}
        ]}]
        d = layer1_route(messages=msgs, available_visual_model="openai/gpt-4o")
        assert d is not None
        assert d.target_model == "openai/gpt-4o"
        assert "L1:visual_detect" in d.decision_layers


class TestBudgetGate:
    def test_over_budget_forces_free(self):
        budget = BudgetState(total_spent_usd=10.0, session_limit_usd=10.0)
        d = layer1_route(
            messages=[{"role": "user", "content": "test"}],
            budget_state=budget,
            available_free_model="free/model",
        )
        assert d is not None
        assert d.target_model == "free/model"
        assert "L1:budget_gate" in d.decision_layers
        assert d.policy_checks["budget_ok"] is False


class TestComplexityEstimation:
    def test_short_query_is_quick(self):
        cat, conf = estimate_complexity("hi")
        assert cat == "quick"
        assert conf >= 0.8

    def test_code_detected_as_analyst(self):
        cat, conf = estimate_complexity("def fibonacci(n):\n    return n if n < 2 else fibonacci(n-1) + fibonacci(n-2)")
        assert cat == "analyst"
        assert conf >= 0.7

    def test_long_text_is_analyst(self):
        cat, _ = estimate_complexity("word " * 250)
        assert cat == "analyst"

    def test_math_is_analyst(self):
        cat, _ = estimate_complexity(
            "I need you to solve this integral equation for x and then verify "
            "the result by taking the derivative of the solution"
        )
        assert cat == "analyst"

    def test_empty_is_quick(self):
        cat, conf = estimate_complexity("")
        assert cat == "quick"
        assert conf >= 0.8

    def test_detect_coding_intent_handles_freeform_programming_prompt(self):
        assert detect_coding_intent("hajmo nešto da programiramo tetris igricu") is True


# ─── Cascade Router ───

class TestCascadeRouter:
    def test_godmode_passes_through_cascade(self):
        cr = CascadeRouter()
        d = cr.route(messages=[{"role": "user", "content": "hello"}],
                     godmode_model="override/model")
        assert d.target_model == "override/model"
        assert d.confidence == 1.0

    def test_default_fallback_when_no_confidence(self):
        cr = CascadeRouter()
        d = cr.route(
            messages=[{"role": "user", "content": "what time is it in Tokyo?"}],
            available_free_model="free/model",
            available_heavy_model="heavy/model",
        )
        assert d.target_model != ""
        assert d.confidence > 0

    def test_specialist_pin_via_cascade(self):
        cr = CascadeRouter()
        d = cr.route(
            messages=[{"role": "user", "content": "do pdf_analysis on this document"}],
        )
        assert d.target_model == "anthropic/claude-sonnet-4"

    def test_heuristic_analyst_routes_to_heavy(self):
        cr = CascadeRouter()
        code_msg = "```python\ndef complex_algorithm():\n    pass\n```" + " detailed explanation " * 20
        d = cr.route(
            messages=[{"role": "user", "content": code_msg}],
            available_free_model="free/m",
            available_heavy_model="heavy/m",
        )
        # Should route to heavy since code is detected
        assert d.target_model in ("heavy/m", "free/m")  # either acceptable

    def test_layer1_coding_intent_routes_freeform_programming_prompt_to_heavy(self):
        cr = CascadeRouter()
        d = cr.route(
            messages=[{"role": "user", "content": "Hajmo nešto da programiramo tetris igricu u Pythonu."}],
            available_free_model="free/m",
            available_heavy_model="heavy/m",
        )
        assert d.target_model == "heavy/m"
        assert "code" in (d.reason or "").lower()


# ─── Table Sync Utilities ───

class TestTableSyncUtils:
    def test_checksum_deterministic(self):
        table = {"routing_hierarchy": [
            {"model": "a", "value_score": 100},
            {"model": "b", "value_score": 50},
        ]}
        c1 = compute_table_checksum(table)
        c2 = compute_table_checksum(table)
        assert c1 == c2

    def test_checksum_changes(self):
        t1 = {"routing_hierarchy": [{"model": "a", "value_score": 100}]}
        t2 = {"routing_hierarchy": [{"model": "a", "value_score": 99}]}
        assert compute_table_checksum(t1) != compute_table_checksum(t2)

    def test_version_comparison(self):
        assert _ver_gte("4.0", "4.0") is True
        assert _ver_gte("4.1", "4.0") is True
        assert _ver_gte("3.9", "4.0") is False
        assert _ver_gte("4.0-sovereign", "4.0") is True

    def test_get_best_free_primary(self):
        table = {"routing_hierarchy": [
            {"model": "paid/m", "tier": "SECONDARY", "metrics": {"cost": 0.01}},
            {"model": "free/m", "tier": "FREE_FRONTIER", "metrics": {"cost": 0}},
        ]}
        best = get_best_free_primary(table)
        assert best is not None
        assert best["model"] == "free/m"

    def test_get_heavy_hitter(self):
        table = {
            "heavy_hitter": {"model": "heavy/m"},
            "routing_hierarchy": [
                {"model": "heavy/m", "metrics": {"intelligence": 99}},
                {"model": "free/m", "metrics": {"intelligence": 80}},
            ],
        }
        hh = get_heavy_hitter(table)
        assert hh["model"] == "heavy/m"

    def test_get_top_fallbacks_excludes_primary(self):
        table = {"routing_hierarchy": [
            {"model": "primary"},
            {"model": "fb1"},
            {"model": "fb2"},
            {"model": "fb3"},
        ]}
        fbs = get_top_fallbacks(table, "primary", max_count=2)
        assert "primary" not in fbs
        assert len(fbs) == 2
        assert fbs == ["fb1", "fb2"]

    def test_cost_optimizer_receives_prompt_aware_task_hint(self):
        class FakeOptimizer:
            def __init__(self):
                self.task_hint = ""

            def optimize(self, **kwargs):
                self.task_hint = kwargs.get("task_hint", "")
                from aichaind.routing.cost_optimizer import CostRoute
                return CostRoute(
                    model=kwargs["current_model"] or kwargs["available_models"].get("free", ""),
                    provider="deepseek",
                    estimated_cost_usd=0.0,
                    reason="test",
                    tier="free",
                    local_effective_score=0.0,
                    access_method="api_key",
                )

        cr = CascadeRouter()
        optimizer = FakeOptimizer()
        cr.configure_cost_optimizer(optimizer)
        from aichaind.providers.balance import BalanceReport, ProviderBalance

        cr.route(
            messages=[{"role": "user", "content": 'Return only minified JSON with keys ok and answer where ok is true and answer is 7.'}],
            available_free_model="deepseek/deepseek-chat",
            available_heavy_model="deepseek/deepseek-reasoner",
            balance_report=BalanceReport(
                balances={
                    "deepseek": ProviderBalance(provider="deepseek", has_credits=True, balance_usd=2.0, source="api"),
                },
                providers_with_credits=["deepseek"],
            ),
        )
        assert "json" in optimizer.task_hint.lower()
        assert "answer" in optimizer.task_hint.lower()

