"""Tier 1 task classifier tests + acceptance gate (PROJECT_STATE roadmap #3):
>= 85% accuracy on the hand-made 50-example set."""
import json
from pathlib import Path

from aichaind.routing.task_classifier import (TASK_TYPES, TAXONOMY_TO_CATALOG,
                                              Classification, classify)

CASES = json.loads((Path(__file__).parent / "data" / "task_classifier_cases.json")
                   .read_text(encoding="utf-8"))


def _run(case) -> Classification:
    return classify(case["messages"],
                    tool_schema_present=case.get("tool_schema_present", False),
                    attachment_types=case.get("attachment_types"))


def test_test_set_has_50_examples_all_types():
    assert len(CASES) == 50
    assert {c["expected"] for c in CASES} == set(TASK_TYPES)


def test_accuracy_at_least_85_percent():
    hits = sum(1 for c in CASES if _run(c).task_type == c["expected"])
    accuracy = hits / len(CASES)
    assert accuracy >= 0.85, f"accuracy {accuracy:.0%} below acceptance threshold"


def test_tool_schema_dominates_everything():
    r = classify([{"role": "user", "content": "fix this ```python\nx=1\n``` bug"}],
                 tool_schema_present=True)
    assert r.task_type == "agentic_tool_use"


def test_image_beats_text_signals():
    r = classify([{"role": "user", "content": "why does this code screenshot fail?"}],
                 attachment_types=["image/png"])
    assert r.task_type.startswith("vision_")


def test_multipart_image_content_detected():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "izvuci tekst sa slike"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]}]
    assert classify(msgs).task_type == "vision_ocr"


def test_difficulty_bounds_and_monotonicity():
    easy = classify([{"role": "user", "content": "zdravo"}])
    hard = classify([{"role": "user", "content":
                      "Prove the theorem rigorously, covering all edge cases, "
                      "for a distributed production system. " * 30}])
    assert 0.0 <= easy.difficulty <= 100.0
    assert 0.0 <= hard.difficulty <= 100.0
    assert hard.difficulty > easy.difficulty


def test_catalog_dimension_mapping_total():
    assert set(TAXONOMY_TO_CATALOG) == set(TASK_TYPES)
    for c in CASES:
        assert _run(c).catalog_dimension  # never raises


def test_fallbacks_harness_then_previous():
    neutral = [{"role": "user", "content":
                "Razmisljao sam o onome sto smo pominjali ranije i mislim da bi "
                "trebalo da nastavimo tamo gde smo stali, ima jos dosta materijala "
                "koji nismo ni pipnuli a vreme polako curi."}]
    assert classify(neutral, harness_hint="coding").task_type == "coding"
    assert classify(neutral, previous_task_type="legal_formal").task_type == "legal_formal"
