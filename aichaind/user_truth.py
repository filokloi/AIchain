#!/usr/bin/env python3
"""
aichaind.user_truth — Subjective truth loader (user_truth.json).

Loads the user's personal situation (profile sliders, budget, assets,
privacy rules) and validates it against schemas/user_truth.schema.json.
Never leaves the machine. API keys are key *references* (keyring/env),
never raw secrets (schema enforces the shape; discovery.py resolves them).

Spec: PROJECT_STATE §2/§3, docs/DYNAMIC_AUTO.md.
MIT License.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from aichaind.pom import Boundary, Budget, Profile

log = logging.getLogger("aichaind.user_truth")

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = _REPO_ROOT / "schemas" / "user_truth.schema.json"

#: Minimal valid document used when the user has no user_truth.json yet.
DEFAULT_TRUTH: dict = {"version": 1, "profile": {"mode": "balanced"}}

#: mode -> (intelligence slider, cost sensitivity slider), both 0..100
_MODE_SLIDERS = {
    "economy": (25.0, 90.0),
    "balanced": (50.0, 50.0),
    "power": (90.0, 15.0),
}


class UserTruthError(ValueError):
    """Raised when user_truth.json exists but is invalid."""


def _validate(truth: dict, schema_path: Path) -> None:
    """Validate against the JSON schema. Uses jsonschema when installed;
    otherwise falls back to checking the schema's `required` keys."""
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    try:
        import jsonschema
    except ImportError:
        log.warning("jsonschema not installed — falling back to minimal "
                    "required-keys validation of user_truth.json")
        missing = [k for k in schema.get("required", []) if k not in truth]
        if missing:
            raise UserTruthError(f"user_truth.json missing required keys: {missing}")
        profile = truth.get("profile")
        if not isinstance(profile, dict) or "mode" not in profile:
            raise UserTruthError("user_truth.json: profile.mode is required")
        return
    try:
        jsonschema.validate(instance=truth, schema=schema)
    except jsonschema.ValidationError as e:
        raise UserTruthError(f"user_truth.json schema violation: {e.message}") from e


def load_user_truth(path: str | Path | None,
                    schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> dict:
    """Load and validate user_truth.json.

    Missing file -> DEFAULT_TRUTH (router degrades to legacy behaviour).
    Present but invalid -> UserTruthError (fail loudly: silent misreads of
    budget/privacy rules are worse than a startup error).
    """
    if path is None:
        return dict(DEFAULT_TRUTH)
    p = Path(path)
    if not p.exists():
        log.info(f"user_truth.json not found at {p} — using defaults")
        return dict(DEFAULT_TRUTH)
    try:
        truth = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UserTruthError(f"user_truth.json is not valid JSON: {e}") from e
    _validate(truth, Path(schema_path))
    log.info(f"user_truth loaded from {p} (mode={truth['profile'].get('mode')})")
    return truth


def profile_from_truth(truth: dict) -> Profile:
    """Map profile.mode / custom_weights to POM sliders."""
    prof = truth.get("profile", {})
    mode = prof.get("mode", "balanced")
    if mode == "custom":
        w = prof.get("custom_weights", {})
        intelligence = float(w.get("intelligence", 0.5)) * 100.0
        cost = float(w.get("cost", 0.5)) * 100.0
    else:
        intelligence, cost = _MODE_SLIDERS.get(mode, _MODE_SLIDERS["balanced"])
    return Profile(
        intelligence=intelligence,
        cost_sensitivity=cost,
        min_intelligence=float(prof.get("min_intelligence", 0.0)),
    )


def budget_from_truth(truth: dict, spent_today: float = 0.0) -> Budget:
    b = truth.get("budget", {})
    daily = b.get("daily_limit")
    return Budget(
        daily_limit=float(daily) if daily is not None else float("inf"),
        spent_today=spent_today,
        soft_threshold=float(b.get("soft_threshold", 0.8)),
        hard_stop=bool(b.get("hard_stop", True)),
    )


def boundary_from_truth(truth: dict, text: str = "", tags: list[str] | None = None) -> Boundary:
    """Resolve the privacy boundary for one request: rule match beats default.

    Privacy is the hard filter nothing overrides (PROJECT_STATE §3), so the
    *strictest* matching rule wins.
    """
    privacy = truth.get("privacy", {})
    strictness = {Boundary.ANY: 0, Boundary.NO_TRAINING_DATA: 1,
                  Boundary.EU_HOSTED_ONLY: 1, Boundary.LOCAL_ONLY: 2}
    result = Boundary(privacy.get("default_boundary", "any"))
    lowered = (text or "").lower()
    tag_set = {t.lower() for t in (tags or [])}
    for rule in privacy.get("rules", []):
        match = rule.get("match", {})
        hit = any(t.lower() in tag_set for t in match.get("tags", [])) or \
              any(k.lower() in lowered for k in match.get("keywords", []))
        if hit:
            candidate = Boundary(rule["boundary"])
            if strictness[candidate] > strictness[result]:
                result = candidate
    return result
