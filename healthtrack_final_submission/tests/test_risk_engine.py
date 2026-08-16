import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import risk_engine
from app.models import RiskLevel


def test_news2_normal_vitals_score_zero():
    score = risk_engine.news2_score(
        heart_rate=75, systolic_bp=120, spo2=98,
        temperature=98.4, respiratory_rate=16, consciousness_level="Alert"
    )
    assert score == 0


def test_news2_critical_vitals_score_high():
    score = risk_engine.news2_score(
        heart_rate=140, systolic_bp=85, spo2=89,
        temperature=103.0, respiratory_rate=30, consciousness_level="Voice"
    )
    # every parameter maxed out should push the aggregate score well past
    # the NEWS2 "high risk" threshold of 7
    assert score >= 15


def test_news2_handles_missing_values():
    # Only some vitals present; should not raise and should return >= 0
    score = risk_engine.news2_score(
        heart_rate=75, systolic_bp=None, spo2=None,
        temperature=None, respiratory_rate=None
    )
    assert score == 0


def test_news2_bradycardia_scores_points():
    score = risk_engine.news2_score(
        heart_rate=35, systolic_bp=120, spo2=98,
        temperature=98.4, respiratory_rate=16
    )
    assert score == 3  # heart rate <40 band


def test_model_probability_loads_and_returns_float():
    proba = risk_engine.model_probability(
        heart_rate=130, systolic_bp=85, diastolic_bp=55,
        spo2=88, temperature=102.5, respiratory_rate=28
    )
    assert proba is not None
    assert 0.0 <= proba <= 1.0


def test_model_probability_higher_for_abnormal_vitals():
    normal = risk_engine.model_probability(
        heart_rate=75, systolic_bp=120, diastolic_bp=80,
        spo2=98, temperature=98.4, respiratory_rate=16
    )
    abnormal = risk_engine.model_probability(
        heart_rate=135, systolic_bp=82, diastolic_bp=50,
        spo2=87, temperature=102.8, respiratory_rate=30
    )
    assert abnormal > normal


def test_assess_returns_critical_for_severe_vitals():
    result = risk_engine.assess(
        heart_rate=135, systolic_bp=82, diastolic_bp=50,
        spo2=87, temperature=102.8, respiratory_rate=30
    )
    assert result.risk_level == RiskLevel.CRITICAL
    assert result.news2 >= 7


def test_assess_returns_low_for_normal_vitals():
    result = risk_engine.assess(
        heart_rate=75, systolic_bp=120, diastolic_bp=80,
        spo2=98, temperature=98.4, respiratory_rate=16
    )
    assert result.risk_level == RiskLevel.LOW


def test_assess_handles_all_missing_vitals_gracefully():
    result = risk_engine.assess()
    assert result.news2 == 0
    assert result.risk_level == RiskLevel.LOW
