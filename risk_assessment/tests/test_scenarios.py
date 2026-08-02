"""
Scenario-based test suite for the HealthTrack Risk Assessment engine.

Run with: python3 -m pytest tests/test_scenarios.py -v
(or: python3 tests/test_scenarios.py  to run as a plain script)

These are not unit tests of implementation details -- they are CLINICAL
SCENARIO tests: given a plausible vitals pattern, does the system classify
risk in the direction a clinician would expect, and does it surface the
right explanatory factors? Exact score thresholds are checked loosely
(ranges / relative comparisons) rather than pinned to brittle exact values,
since the point-banding may reasonably be retuned during clinical review.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from engine.data_synthetic import generate_scenario_patients
from engine.risk_scoring import score_reading
from engine.risk_classification import classify_dict
from engine.pattern_recognition import (
    detect_multi_metric_pattern, personal_baseline_anomalies, population_outliers, compute_reference_stats,
)
from engine.trend_analysis import compute_trend, rolling_trend_series
from engine.risk_factors import identify_risk_factors
from engine.recommendations import generate_recommendations
from engine.report_generator import build_patient_report, render_html_report


SCENARIOS = generate_scenario_patients()


# ---------------------------------------------------------------------------
# 1. Risk scoring: stable/healthy patient should score Low
# ---------------------------------------------------------------------------
def test_stable_healthy_scores_low():
    reading = SCENARIOS["stable_healthy_adult"].iloc[0].to_dict()
    result = score_reading(reading)
    level = classify_dict(result.total_score, [m.points for m in result.metric_scores])
    assert result.total_score <= 2, f"expected near-zero score, got {result.total_score}"
    assert level["label"] == "Low"


# ---------------------------------------------------------------------------
# 2. Hypoxia pattern (COPD patient) should be flagged and score meaningfully
# ---------------------------------------------------------------------------
def test_hypoxia_pattern_detected():
    reading = SCENARIOS["hypoxia_copd_patient"].iloc[0].to_dict()
    result = score_reading(reading)
    spo2_score = next(m for m in result.metric_scores if m.metric == "spo2")
    rr_score = next(m for m in result.metric_scores if m.metric == "respiratory_rate")
    assert spo2_score.points >= 2
    assert rr_score.points >= 2

    patterns = detect_multi_metric_pattern({m.metric: m.points for m in result.metric_scores})
    assert "possible_respiratory_failure" in patterns


# ---------------------------------------------------------------------------
# 3. Sepsis-like compound pattern should be recognized and escalate risk
# ---------------------------------------------------------------------------
def test_sepsis_pattern_escalates_risk():
    reading = SCENARIOS["sepsis_pattern"].iloc[0].to_dict()
    result = score_reading(reading)
    metric_points = [m.points for m in result.metric_scores]
    level = classify_dict(result.total_score, metric_points)

    patterns = detect_multi_metric_pattern({m.metric: m.points for m in result.metric_scores})
    assert "possible_sepsis_pattern" in patterns
    assert level["label"] in ("High", "Critical")

    factors = identify_risk_factors(reading)
    assert len(factors["compound_patterns"]) >= 1
    recs = generate_recommendations(factors, level["label"])
    bases = {r["basis"] for r in recs["recommendations"]}
    assert "compound_pattern" in bases


# ---------------------------------------------------------------------------
# 4. Hypertensive crisis: single red-flag parameter should escalate
#    classification even though other metrics are normal
# ---------------------------------------------------------------------------
def test_hypertensive_crisis_red_flag_escalation():
    reading = SCENARIOS["hypertensive_crisis"].iloc[0].to_dict()
    result = score_reading(reading)
    metric_points = [m.points for m in result.metric_scores]
    sbp_score = next(m for m in result.metric_scores if m.metric == "systolic_bp")
    assert sbp_score.points == 3

    level = classify_dict(result.total_score, metric_points)
    # even if total score alone might land in Moderate, red-flag rule must escalate to High+
    assert level["label"] in ("High", "Critical")


# ---------------------------------------------------------------------------
# 5. Hypoglycemia in a diabetic patient should trigger the correct recommendation
# ---------------------------------------------------------------------------
def test_hypoglycemia_triggers_glucose_recommendation():
    reading = SCENARIOS["hypoglycemia_diabetic"].iloc[0].to_dict()
    result = score_reading(reading)
    level = classify_dict(result.total_score, [m.points for m in result.metric_scores])
    factors = identify_risk_factors(reading)
    recs = generate_recommendations(factors, level["label"])
    bases = {r["basis"] for r in recs["recommendations"]}
    assert "hypoglycemia" in bases


# ---------------------------------------------------------------------------
# 6. Trend analysis: a worsening trajectory must be detected as "worsening"
# ---------------------------------------------------------------------------
def test_worsening_trend_detected():
    df = SCENARIOS["worsening_trend"]
    scores = [score_reading(r.to_dict()).total_score for _, r in df.iterrows()]
    trend = compute_trend(scores)
    assert trend["direction"] == "worsening"
    assert trend["slope"] > 0


def test_stable_trend_detected_for_flat_scores():
    trend = compute_trend([2, 2, 3, 2, 2, 3])
    assert trend["direction"] == "stable"


def test_insufficient_data_handled_gracefully():
    trend = compute_trend([2, 3])
    assert trend["direction"] == "insufficient_data"


# ---------------------------------------------------------------------------
# 7. Missing data should not silently pass as "normal" -- confirm it's
#    marked as not measured and does not contribute false-negative safety
# ---------------------------------------------------------------------------
def test_missing_data_marked_not_measured():
    reading = SCENARIOS["missing_data"].iloc[0].to_dict()
    result = score_reading(reading)
    sbp = next(m for m in result.metric_scores if m.metric == "systolic_bp")
    temp = next(m for m in result.metric_scores if m.metric == "temperature_c")
    assert sbp.band == "not measured"
    assert temp.band == "not measured"
    assert sbp.points == 0 and temp.points == 0  # documented limitation: missing != 0 points, see docs


# ---------------------------------------------------------------------------
# 8. Personal-baseline anomaly detection: a patient whose own history is
#    stable, then spikes, should be flagged even if the spike is within
#    "generic normal" range
# ---------------------------------------------------------------------------
def test_personal_baseline_anomaly_detected():
    history = pd.DataFrame({
        "heart_rate": [58, 60, 59, 57, 61, 58, 60, 59, 58, 60, 95],  # last reading is a big personal jump
    })
    scored = personal_baseline_anomalies(history, metrics=["heart_rate"], window=10)
    last_row = scored.iloc[-1]
    assert bool(last_row["heart_rate_anomaly"]) is True


def test_population_outlier_detection():
    ref_df = pd.DataFrame({"heart_rate": [70, 72, 68, 71, 69, 73, 70, 72, 69, 71] * 5})
    stats = compute_reference_stats(ref_df, ["heart_rate"])
    result = population_outliers({"heart_rate": 160}, stats)
    assert result["heart_rate"]["is_outlier"] is True

    result_normal = population_outliers({"heart_rate": 71}, stats)
    assert result_normal["heart_rate"]["is_outlier"] is False


# ---------------------------------------------------------------------------
# 9. Full end-to-end report generation should not error and should contain
#    all required sections (regression test for integration)
# ---------------------------------------------------------------------------
def test_full_report_generation_end_to_end():
    reading = SCENARIOS["sepsis_pattern"].iloc[0].to_dict()
    report = build_patient_report(
        patient_id="TEST-001",
        patient_name="Test Patient",
        reading=reading,
        history_scores=[2, 3, 5, 7],
        history_timestamps=[0, 6, 12, 18, 24],
        model_probability=0.62,
    )
    assert report["risk_level"]["label"] in ("High", "Critical")
    assert report["trend"]["direction"] in ("worsening", "stable", "improving")
    assert len(report["recommendations"]["recommendations"]) > 0
    assert "SYSTEM-GENERATED" in report["recommendations"]["disclaimer"]

    html = render_html_report(report)
    assert "Test Patient" in html
    assert "SYSTEM-GENERATED" in html


# ---------------------------------------------------------------------------
# 10. Recommendations must never contain a definitive diagnostic statement
#     -- guard against language drifting into "you have X" territory
# ---------------------------------------------------------------------------
def test_recommendations_are_suggestions_not_diagnoses():
    reading = SCENARIOS["sepsis_pattern"].iloc[0].to_dict()
    factors = identify_risk_factors(reading)
    recs = generate_recommendations(factors, "High")
    banned_phrases = ["you have", "diagnosed with", "patient has sepsis", "confirmed diagnosis"]
    all_text = " ".join(r["recommendation"] for r in recs["recommendations"]).lower()
    for phrase in banned_phrases:
        assert phrase not in all_text
    assert "SYSTEM-GENERATED" in recs["disclaimer"]


if __name__ == "__main__":
    import inspect
    test_fns = [f for name, f in list(globals().items()) if name.startswith("test_") and callable(f)]
    passed, failed = 0, 0
    for fn in test_fns:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {fn.__name__} -- {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {fn.__name__} -- {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(test_fns)}")
