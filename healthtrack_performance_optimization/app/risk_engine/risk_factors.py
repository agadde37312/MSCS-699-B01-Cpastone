"""
Risk Factor Identification
-----------------------------
Explains WHY a patient's risk score/prediction came out the way it did, in
clinician-readable terms, by combining:
  1. The rule-based per-metric scores (direct, deterministic attribution)
  2. Any compound patterns detected (pattern_recognition.detect_multi_metric_pattern)
  3. Personal-baseline anomalies (this reading vs. this patient's own recent history)
  4. The predictive model's global feature importances, used to explain
     which inputs the MODEL leans on in general (not a per-prediction
     SHAP explanation -- see docs/model_validation.md limitations section)
"""

from app.risk_engine.risk_scoring import score_reading
from app.risk_engine.pattern_recognition import detect_multi_metric_pattern

METRIC_LABELS = {
    "heart_rate": "Heart rate",
    "systolic_bp": "Systolic blood pressure",
    "diastolic_bp": "Diastolic blood pressure",
    "spo2": "Oxygen saturation (SpO2)",
    "temperature_c": "Body temperature",
    "respiratory_rate": "Respiratory rate",
    "blood_glucose": "Blood glucose",
}

PATTERN_LABELS = {
    "possible_sepsis_pattern": "Combination of elevated heart rate, low blood pressure, and fever/high temperature — a pattern consistent with possible sepsis and warranting urgent review.",
    "possible_respiratory_failure": "Combination of low oxygen saturation and elevated respiratory rate — a pattern consistent with possible respiratory compromise.",
    "possible_hemodynamic_instability": "Low blood pressure with elevated heart rate and normal glucose — a pattern consistent with possible hemodynamic instability (e.g., volume loss, cardiac cause).",
    "possible_glycemic_crisis": "Abnormal blood glucose together with elevated heart rate — a pattern consistent with a possible glycemic crisis (hypo- or hyperglycemia).",
    "possible_hypertensive_crisis": "Severely elevated blood pressure with otherwise normal respiratory/oxygenation readings — a pattern consistent with a hypertensive crisis.",
}


def identify_risk_factors(reading: dict, patient_history_row: dict = None) -> dict:
    """
    reading: current vitals dict
    patient_history_row: optional row (e.g., from pattern_recognition
        personal_baseline_anomalies output) with `<metric>_zscore` /
        `<metric>_anomaly` fields, if available

    Returns a structured explanation:
      {
        "contributing_factors": [ {metric, value, points, band, explanation} ... ]
          sorted by points contributed (highest first), points > 0 only
        "compound_patterns": [ {pattern, explanation} ... ]
        "baseline_deviations": [ {metric, zscore, explanation} ... ]
      }
    """
    scoring_result = score_reading(reading)

    contributing_factors = []
    for ms in scoring_result.metric_scores:
        if ms.points > 0:
            label = METRIC_LABELS.get(ms.metric, ms.metric)
            contributing_factors.append({
                "metric": ms.metric,
                "label": label,
                "value": ms.value,
                "points": ms.points,
                "band": ms.band,
                "explanation": f"{label} of {ms.value} is classified as '{ms.band}', "
                                f"contributing {ms.points} of {scoring_result.max_possible // 7} "
                                f"possible points on this metric to the overall risk score.",
            })
    contributing_factors.sort(key=lambda f: f["points"], reverse=True)

    metric_points = {ms.metric: ms.points for ms in scoring_result.metric_scores}
    compound = detect_multi_metric_pattern(metric_points)
    compound_patterns = [
        {"pattern": p, "explanation": PATTERN_LABELS.get(p, p)} for p in compound
    ]

    baseline_deviations = []
    if patient_history_row is not None:
        for metric in METRIC_LABELS:
            z_key = f"{metric}_zscore"
            anomaly_key = f"{metric}_anomaly"
            if patient_history_row.get(anomaly_key):
                z = patient_history_row.get(z_key)
                direction = "higher" if (z is not None and z > 0) else "lower"
                baseline_deviations.append({
                    "metric": metric,
                    "label": METRIC_LABELS.get(metric, metric),
                    "zscore": round(float(z), 2) if z is not None else None,
                    "explanation": f"{METRIC_LABELS.get(metric, metric)} is unusually {direction} "
                                    f"compared to this patient's own recent baseline "
                                    f"(z-score {round(float(z), 2) if z is not None else 'n/a'}), "
                                    f"even if the absolute value looks acceptable on a general chart.",
                })

    return {
        "total_score": scoring_result.total_score,
        "max_possible": scoring_result.max_possible,
        "contributing_factors": contributing_factors,
        "compound_patterns": compound_patterns,
        "baseline_deviations": baseline_deviations,
    }
