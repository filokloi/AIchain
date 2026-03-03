import pytest
from arbitrator import assess_geopolitical_risk, compute_value_score, parse_cost

def test_geopolitical_risk_assessment():
    assert assess_geopolitical_risk("openai/gpt-4o") == "LOW"
    assert assess_geopolitical_risk("deepseek/deepseek-r1") == "HIGH"
    assert assess_geopolitical_risk("qwen/qwen-max") == "HIGH"
    assert assess_geopolitical_risk("mistralai/mistral-large") == "MEDIUM"
    assert assess_geopolitical_risk("google/gemini-2.5-pro") == "LOW"

def test_value_score_calculation():
    # Free model vs Paid model
    free_score = compute_value_score(90, 80, 90, 0.0)
    paid_score = compute_value_score(90, 80, 90, 10.0)
    assert free_score > paid_score
    
    # Stability impact
    high_stab = compute_value_score(90, 80, 90, 0.0)
    low_stab = compute_value_score(90, 80, 50, 0.0)
    assert high_stab > low_stab

def test_cost_parsing():
    pricing = {"prompt": "0.000001", "completion": "0.000002"}
    assert parse_cost(pricing) == 0.0000015
    
    assert parse_cost({}) == 0.0
    assert parse_cost(None) == 0.0
