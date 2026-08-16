"""
Risk assessment engine.

Combines a NEWS2-inspired (National Early Warning Score 2) rule-based score,
which is transparent and clinician-checkable, with a Random Forest model
that estimates the probability of the patient being high-risk from the same
vitals. The two are combined into a final RiskLevel used for alerting.

NEWS2 reference (simplified, standard aggregate scoring bands):
Royal College of Physicians. (2017). National Early Warning Score (NEWS) 2.
https://www.rcp.ac.uk/improving-care/resources/national-early-warning-score-news-2/
"""
import os
from dataclasses import dataclass
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from app.models import RiskLevel

MODEL_PATH = os.getenv(
    "RISK_MODEL_PATH",
    os.path.join(os.path.dirname(__file__), "risk_model.joblib"),
)

_model_cache = None


def _score_band(value, bands):
    """bands: list of (low, high, points), low/high inclusive; None = open end."""
    for low, high, points in bands:
        if (low is None or value >= low) and (high is None or value <= high):
            return points
    return 0


def news2_score(
    heart_rate: Optional[float],
    systolic_bp: Optional[float],
    spo2: Optional[float],
    temperature: Optional[float],
    respiratory_rate: Optional[float],
    consciousness_level: Optional[str] = "Alert",
) -> int:
    """Simplified NEWS2 aggregate score (0-20+) from available vitals.

    Missing individual readings are scored as 0 rather than failing the
    whole assessment, since real-world monitoring feeds are not always
    complete for every parameter at every tick.
    """
    total = 0

    if respiratory_rate is not None:
        total += _score_band(respiratory_rate, [
            (None, 8, 3), (9, 11, 1), (12, 20, 0), (21, 24, 2), (25, None, 3)
        ])
    if spo2 is not None:
        total += _score_band(spo2, [
            (None, 91, 3), (92, 93, 2), (94, 95, 1), (96, None, 0)
        ])
    if systolic_bp is not None:
        total += _score_band(systolic_bp, [
            (None, 90, 3), (91, 100, 2), (101, 110, 1), (111, 219, 0), (220, None, 3)
        ])
    if heart_rate is not None:
        total += _score_band(heart_rate, [
            (None, 40, 3), (41, 50, 1), (51, 90, 0), (91, 110, 1), (111, 130, 2), (131, None, 3)
        ])
    if temperature is not None:
        total += _score_band(temperature, [
            (None, 95.0, 3), (95.1, 96.8, 1), (96.9, 100.4, 0), (100.5, 102.2, 1), (102.3, None, 2)
        ])
    if consciousness_level and consciousness_level != "Alert":
        total += 3

    return total


def _load_model():
    global _model_cache
    if _model_cache is None and os.path.exists(MODEL_PATH):
        _model_cache = joblib.load(MODEL_PATH)
    return _model_cache


def model_probability(
    heart_rate, systolic_bp, diastolic_bp, spo2, temperature, respiratory_rate
) -> Optional[float]:
    """Return the Random Forest's predicted probability of High risk, or
    None if the model artifact hasn't been trained/loaded yet."""
    model = _load_model()
    if model is None:
        return None
    feats = pd.DataFrame([{
        "heart_rate": heart_rate or 0, "systolic_bp": systolic_bp or 0,
        "diastolic_bp": diastolic_bp or 0, "spo2": spo2 or 0,
        "temperature": temperature or 0, "resp_rate": respiratory_rate or 0,
    }])
    proba = model.predict_proba(feats)[0]
    # class order: model.classes_ tells us which column is "High"
    classes = list(model.classes_)
    if "High" in classes:
        return float(proba[classes.index("High")])
    return float(proba[-1])


@dataclass
class Assessment:
    news2: int
    probability: Optional[float]
    risk_level: RiskLevel


def assess(
    heart_rate=None, systolic_bp=None, diastolic_bp=None,
    spo2=None, temperature=None, respiratory_rate=None,
    consciousness_level="Alert",
) -> Assessment:
    score = news2_score(
        heart_rate, systolic_bp, spo2, temperature, respiratory_rate, consciousness_level
    )
    proba = model_probability(
        heart_rate, systolic_bp, diastolic_bp, spo2, temperature, respiratory_rate
    )

    # Combine rule-based NEWS2 bands with the model's probability (if available).
    # NEWS2 clinical bands: 0-4 low, 5-6 moderate (or any single param = 3),
    # 7+ high. The RF probability can escalate a borderline NEWS2 score.
    if score >= 7:
        level = RiskLevel.CRITICAL
    elif score >= 5:
        level = RiskLevel.HIGH if (proba is not None and proba >= 0.6) else RiskLevel.MODERATE
    elif score >= 3:
        level = RiskLevel.MODERATE if (proba is None or proba < 0.5) else RiskLevel.HIGH
    else:
        level = RiskLevel.LOW

    return Assessment(news2=score, probability=proba, risk_level=level)
