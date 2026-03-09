#!/usr/bin/env python3
"""
aichaind.routing.cascade — 4-Layer Cascading Router

Orchestrates the multi-layer routing decision pipeline:
    Layer 1: Rules + Heuristics (0ms)          — always active
    Layer 2: Semantic Pre-routing (1-5ms)      — always active
    Layer 3: Local TF-IDF Encoder (1-5ms)      — v2.0, feature-flagged
    Layer 4: Cloud Classifier (200-2000ms)     — v2.0, feature-flagged

Cascade stops when any layer returns confidence >= threshold.
Phase 7 adds balance-aware cost optimization after the preference is known.
"""

import time
import logging
from typing import Optional

from aichaind.routing.rules import layer1_route, RouteDecision
from aichaind.routing.semantic import semantic_preroute
from aichaind.routing.encoder import LocalEncoderScorer
from aichaind.routing.cloud_classifier import CloudClassifier

log = logging.getLogger("aichaind.routing.cascade")

CONFIDENCE_THRESHOLD = 0.85
_DIRECT_PROVIDERS = {
    "openai", "google", "anthropic", "deepseek", "groq",
    "mistral", "xai", "cohere", "moonshot", "zhipu", "openrouter",
}


class CascadeRouter:
    """
    Orchestrates multi-layer routing.
    Each layer is optional. Cascade stops when confidence >= threshold.
    """

    def __init__(self, cfg: dict = None):
        self.cfg = cfg or {}
        self.confidence_threshold = self.cfg.get("confidence_threshold", CONFIDENCE_THRESHOLD)

        # Layer flags
        self._layer2_enabled = True
        self._layer3_enabled = self.cfg.get("layer3_enabled", True)
        self._layer4_enabled = self.cfg.get("layer4_enabled", False)

        # Layer 3: Local encoder (init on first use)
        self._encoder: LocalEncoderScorer | None = None
        if self._layer3_enabled:
            try:
                self._encoder = LocalEncoderScorer()
            except Exception as e:
                log.warning(f"Layer 3 init failed: {e}")
                self._layer3_enabled = False

        # Layer 4: Cloud classifier
        self._cloud: CloudClassifier | None = None
        if self._layer4_enabled:
            self._cloud = CloudClassifier(
                timeout_ms=self.cfg.get("cloud_timeout_ms", 2000)
            )

        # Phase 7: cost optimization hook
        self._cost_optimizer = None

    def configure_cloud(self, adapter, model: str):
        """Configure Layer 4 cloud classifier with an adapter and model."""
        if self._cloud:
            self._cloud.configure(adapter, model)
            self._layer4_enabled = True

    def configure_cost_optimizer(self, optimizer):
        """Attach a Phase 7 cost optimizer instance."""
        self._cost_optimizer = optimizer

    def route(
        self,
        messages: list[dict],
        godmode_model: Optional[str] = None,
        specialist_pins: dict = None,
        budget_state=None,
        privacy_context=None,
        available_free_model: str = "",
        available_heavy_model: str = "",
        available_visual_model: str = "",
        balance_report=None,
    ) -> RouteDecision:
        """Run the cascading router. Returns the best RouteDecision."""
        start_t = time.time()
        all_layers = []
        available_models = {
            "free": available_free_model,
            "heavy": available_heavy_model,
            "visual": available_visual_model,
        }

        def _resolve_model(preference: str) -> str:
            if preference == "heavy":
                return available_heavy_model
            if preference == "visual":
                return available_visual_model
            return available_free_model

        def _finalize(decision: RouteDecision, model_preference: str = "") -> RouteDecision:
            if model_preference and not getattr(decision, "model_preference", ""):
                decision.model_preference = model_preference
            if not decision.target_model and model_preference:
                decision.target_model = _resolve_model(model_preference)
            decision = self._apply_cost_optimization(
                decision=decision,
                model_preference=model_preference,
                available_models=available_models,
                balance_report=balance_report,
                messages=messages,
            )
            if all_layers:
                decision.decision_layers = all_layers
            decision.latency_ms = (time.time() - start_t) * 1000
            return decision

        # ── Layer 1: Rules + Heuristics (always active) ──
        decision = layer1_route(
            messages=messages,
            godmode_model=godmode_model,
            specialist_pins=specialist_pins,
            budget_state=budget_state,
            privacy_context=privacy_context,
            available_free_model=available_free_model,
            available_visual_model=available_visual_model,
        )

        if decision and decision.confidence >= self.confidence_threshold:
            preference = self._infer_preference(decision, available_models)
            if not decision.target_model and "heuristic" in decision.reason:
                decision.target_model = _resolve_model(preference)
            return _finalize(decision, preference)

        if decision:
            all_layers.extend(decision.decision_layers)

        # ── Layer 2: Semantic Pre-routing ──
        if self._layer2_enabled:
            semantic = semantic_preroute(messages)
            if semantic and semantic.confidence >= 0.75:
                l2_tag = f"L2:semantic:{semantic.cluster_name}"
                all_layers.append(l2_tag)
                combined = max(decision.confidence if decision else 0, semantic.confidence)

                if combined >= self.confidence_threshold:
                    return _finalize(
                        RouteDecision(
                            target_model=_resolve_model(semantic.model_preference),
                            confidence=combined,
                            decision_layers=all_layers,
                            reason=f"semantic_{semantic.cluster_name}",
                        ),
                        semantic.model_preference,
                    )

        # ── Layer 3: Local Encoder Scorer ──
        if self._layer3_enabled and self._encoder:
            enc_result = self._encoder.score(messages)
            if enc_result and enc_result.confidence >= 0.7:
                l3_tag = f"L3:encoder:{enc_result.category}"
                all_layers.append(l3_tag)
                prev_best = decision.confidence if decision else 0
                combined = max(prev_best, enc_result.confidence)

                if combined >= self.confidence_threshold:
                    return _finalize(
                        RouteDecision(
                            target_model=_resolve_model(enc_result.model_preference),
                            confidence=combined,
                            decision_layers=all_layers,
                            reason=f"encoder_{enc_result.category}",
                        ),
                        enc_result.model_preference,
                    )

        # ── Layer 4: Cloud Classifier ──
        if self._layer4_enabled and self._cloud:
            cloud_result = self._cloud.classify(messages)
            if cloud_result and cloud_result.confidence >= 0.8:
                l4_tag = f"L4:cloud:{cloud_result.category}"
                all_layers.append(l4_tag)
                return _finalize(
                    RouteDecision(
                        target_model=_resolve_model(cloud_result.model_preference),
                        confidence=cloud_result.confidence,
                        decision_layers=all_layers,
                        reason=f"cloud_{cloud_result.category}",
                    ),
                    cloud_result.model_preference,
                )

        # ── Fallback ──
        if decision:
            preference = self._infer_preference(decision, available_models)
            if not decision.target_model:
                decision.target_model = _resolve_model(preference)
            if not decision.decision_layers:
                decision.decision_layers = all_layers or decision.decision_layers
            return _finalize(decision, preference)

        return _finalize(
            RouteDecision(
                target_model=available_free_model or available_heavy_model,
                confidence=0.5,
                decision_layers=["L0:default_fallback"],
                reason="no_layer_decided",
            ),
            "free" if available_free_model else "heavy",
        )

    def _apply_cost_optimization(
        self,
        decision: RouteDecision,
        model_preference: str,
        available_models: dict,
        balance_report,
        messages: list[dict],
    ) -> RouteDecision:
        if not decision.target_provider and decision.target_model:
            decision.target_provider = self._infer_provider(decision.target_model)

        effective_preference = model_preference or self._infer_preference(decision, available_models)
        decision.model_preference = effective_preference

        if not self._cost_optimizer or not balance_report or not effective_preference:
            return decision
        if not self._should_optimize(decision):
            return decision

        optimized = self._cost_optimizer.optimize(
            model_preference=effective_preference,
            balance_report=balance_report,
            available_models=available_models,
            estimated_tokens=self._estimate_tokens(messages),
        )
        if not optimized or not optimized.model:
            return decision

        decision.target_model = optimized.model
        decision.target_provider = optimized.provider or self._infer_provider(optimized.model)
        decision.estimated_cost_usd = optimized.estimated_cost_usd
        decision.cost_tier = optimized.tier
        if optimized.reason:
            if decision.reason:
                decision.reason = f"{decision.reason}|{optimized.reason}"
            else:
                decision.reason = optimized.reason
        return decision

    def _should_optimize(self, decision: RouteDecision) -> bool:
        reason = (decision.reason or "").lower()
        if reason == "godmode_active":
            return False
        if reason.startswith("specialist_"):
            return False
        return True

    def _infer_preference(self, decision: RouteDecision, available_models: dict) -> str:
        reason = (decision.reason or "").lower()
        target_model = decision.target_model or ""

        if target_model and target_model == available_models.get("visual"):
            return "visual"
        if any(token in reason for token in ("visual", "image", "vision", "screenshot")):
            return "visual"

        if target_model and target_model == available_models.get("heavy"):
            return "heavy"
        if any(token in reason for token in (
            "analyst", "heavy", "code", "research", "theorem", "proof",
            "security", "exploit", "cloud_heavy", "encoder_heavy", "semantic_deep",
        )):
            return "heavy"

        if target_model and target_model == available_models.get("free"):
            return "free"
        if any(token in reason for token in ("quick", "simple", "casual", "free", "budget")):
            return "free"

        if available_models.get("free"):
            return "free"
        if available_models.get("heavy"):
            return "heavy"
        return "visual"

    def _infer_provider(self, model_id: str) -> str:
        if not model_id:
            return "unknown"
        prefix = model_id.split("/")[0].lower() if "/" in model_id else model_id.lower()
        if prefix in _DIRECT_PROVIDERS:
            return prefix
        return "openrouter"

    def _estimate_tokens(self, messages: list[dict]) -> int:
        chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        chars += len(part.get("text", ""))
        return max(128, chars // 4 if chars else 128)
