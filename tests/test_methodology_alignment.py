"""Contract: docs/METHODOLOGY.md describes the shipped pipeline
(PROJECT_STATE roadmap #4 acceptance: every published number must be
derivable from the document). If you change SCORING_WEIGHTS, defaults, or
the rank ordering, update the document — this test will remind you."""
import json
import math
import re
from pathlib import Path

import pytest

from tools.catalog_pipeline.constants import (SCORING_DISPLAY_FORMULA,
                                              SCORING_VERSION, SCORING_WEIGHTS)
from tools.catalog_pipeline.rank import scoring

REPO = Path(__file__).parent.parent
DOC = (REPO / "docs" / "METHODOLOGY.md").read_text(encoding="utf-8")


def test_doc_formula_matches_code_weights():
    """The formula printed in the doc must carry exactly SCORING_WEIGHTS."""
    section = DOC.split("## 4.")[1].split("## 5.")[0]
    for dim, w in SCORING_WEIGHTS.items():
        token = f"{w:.2f}"
        assert token in section, f"weight {dim}={w} not stated in METHODOLOGY §4"
    assert SCORING_VERSION in DOC, "scoring version not referenced in doc"


def test_doc_documents_normalization_defaults():
    """Defaults used by scoring.py must be stated in §3."""
    section = DOC.split("## 3.")[1].split("## 4.")[0]
    for default in ("70", "62", "72", "75", "60", "4096", "+8"):
        assert default in section, f"default {default} missing from METHODOLOGY §3"


def test_doc_documents_tiers():
    for tier in ("OAUTH_BRIDGE", "FREE_FRONTIER", "HEAVY_HITTER"):
        assert tier in DOC, f"tier {tier} missing from METHODOLOGY"


def test_weights_sum_to_one():
    assert math.isclose(sum(SCORING_WEIGHTS.values()), 1.0, abs_tol=1e-9)


def test_display_formula_matches_weights():
    for dim, w in SCORING_WEIGHTS.items():
        assert f"{w:.2f}" in SCORING_DISPLAY_FORMULA


def test_published_score_breakdown_reproducible():
    """Acceptance in the doc's own words: every catalog number must be
    traceable. score_breakdown entries = normalized × weight, and their sum
    equals value_score (±rounding)."""
    table = json.loads((REPO / "ai_routing_table.json").read_text(encoding="utf-8"))
    entries = table.get("routing_hierarchy", [])[:25]
    assert entries, "routing table empty"
    for e in entries:
        nm, bd = e["normalized_metrics"], e["score_breakdown"]
        for dim, w in SCORING_WEIGHTS.items():
            assert bd[dim] == pytest.approx(round(nm[dim] * w, 2), abs=0.011), \
                f"{e['model']}: {dim} breakdown not normalized*weight"
        assert sum(bd.values()) == pytest.approx(e["value_score"], abs=0.1), \
            f"{e['model']}: breakdown does not sum to value_score"


def test_normalization_functions_behave_as_documented():
    # free cost -> 100
    assert scoring._normalized_cost_efficiency(0.0, 0.01) == 100.0
    # most expensive model in snapshot -> 0 (log10(1+9)=1)
    assert scoring._normalized_cost_efficiency(0.01, 0.01) == pytest.approx(0.0)
    # unknown speed -> 62
    assert scoring._normalized_speed(None, 100.0) == 62.0
    # context floor 4096 -> 0
    assert scoring._normalized_context(4096, 1_000_000) == 0.0
    # max context -> 100
    assert scoring._normalized_context(1_000_000, 1_000_000) == pytest.approx(100.0)


def test_planned_section_exists_for_unimplemented_claims():
    """v1.0 aspirations must be explicitly labeled as not-yet-implemented."""
    assert "## 9. Planned" in DOC
    planned = DOC.split("## 9. Planned")[1]
    for claim in ("Median", "0.3×input", "fetched_at", "Per-role composite"):
        assert claim in planned, f"unimplemented claim '{claim}' not in §9"
