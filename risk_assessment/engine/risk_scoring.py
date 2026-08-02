"""
Health Risk Scoring Engine
---------------------------
Scores individual vital-sign metrics on a 0-3 point scale per metric, using
threshold bands adapted from the Royal College of Physicians' National Early
Warning Score 2 (NEWS2) -- a validated, widely-used early-warning scoring
system in remote/ward patient monitoring. Thresholds are ADAPTED (not a
verbatim reproduction of the RCP scoring tables) and extended with blood
glucose banding, since NEWS2 itself does not score glucose.

This module intentionally uses transparent, rule-based scoring (rather than
a black-box model) for the per-metric layer, because point-of-care risk
scores must be explainable to clinicians -- every point awarded is traceable
to a specific vital sign and threshold.

Reference (for the underlying clinical concept, not code):
Royal College of Physicians (2017). National Early Warning Score (NEWS) 2.
"""

from dataclasses import dataclass, field


@dataclass
class MetricScore:
    metric: str
    value: float
    points: int
    band: str  # human-readable band, e.g. "severely abnormal (high)"


@dataclass
class ScoringResult:
    total_score: int
    max_possible: int
    metric_scores: list = field(default_factory=list)

    def as_dict(self):
        return {
            "total_score": self.total_score,
            "max_possible": self.max_possible,
            "metric_scores": [ms.__dict__ for ms in self.metric_scores],
        }


def _band_score(value, bands):
    """
    bands: list of (low, high, points, label) tuples, evaluated in order.
    `low`/`high` are inclusive; use None for open-ended.
    """
    if value is None:
        return 0, "not measured"
    for low, high, points, label in bands:
        if (low is None or value >= low) and (high is None or value <= high):
            return points, label
    return 0, "unclassified"


# Threshold bands per metric. Adapted from NEWS2 physiological banding
# (respiratory rate, SpO2, temperature, systolic BP, heart rate) plus a
# glucose band not present in NEWS2.
BANDS = {
    "respiratory_rate": [
        (None, 8, 3, "critically low"),
        (9, 11, 1, "low"),
        (12, 20, 0, "normal"),
        (21, 24, 2, "elevated"),
        (25, None, 3, "critically high"),
    ],
    "spo2": [
        (None, 91, 3, "critically low"),
        (92, 93, 2, "low"),
        (94, 95, 1, "borderline"),
        (96, None, 0, "normal"),
    ],
    "systolic_bp": [
        (None, 90, 3, "critically low"),
        (91, 100, 2, "low"),
        (101, 110, 1, "borderline low"),
        (111, 179, 0, "normal"),
        (180, None, 3, "hypertensive crisis"),
    ],
    "diastolic_bp": [
        (None, 40, 3, "critically low"),
        (41, 59, 1, "low"),
        (60, 90, 0, "normal"),
        (91, 119, 1, "elevated"),
        (120, None, 3, "hypertensive crisis"),
    ],
    "heart_rate": [
        (None, 40, 3, "critically low"),
        (41, 50, 1, "low"),
        (51, 90, 0, "normal"),
        (91, 110, 1, "elevated"),
        (111, 130, 2, "high"),
        (131, None, 3, "critically high"),
    ],
    "temperature_c": [
        (None, 35.0, 3, "hypothermia"),
        (35.1, 36.0, 1, "low"),
        (36.1, 38.0, 0, "normal"),
        (38.1, 39.0, 1, "fever"),
        (39.1, None, 2, "high fever"),
    ],
    "blood_glucose": [
        (None, 54, 3, "severe hypoglycemia"),
        (55, 69, 2, "hypoglycemia"),
        (70, 140, 0, "normal"),
        (141, 199, 1, "elevated"),
        (200, 300, 2, "hyperglycemia"),
        (301, None, 3, "severe hyperglycemia"),
    ],
}

MAX_PER_METRIC = 3


def score_reading(reading: dict) -> ScoringResult:
    """
    reading: dict with any subset of the keys in BANDS (missing keys score 0
    and are marked "not measured" so callers can see incomplete data rather
    than silently treating it as healthy).
    """
    metric_scores = []
    total = 0
    for metric, bands in BANDS.items():
        value = reading.get(metric)
        points, label = _band_score(value, bands)
        metric_scores.append(MetricScore(metric=metric, value=value, points=points, band=label))
        total += points

    return ScoringResult(
        total_score=total,
        max_possible=MAX_PER_METRIC * len(BANDS),
        metric_scores=metric_scores,
    )


def score_series(readings: list) -> list:
    """Score a list of reading dicts (e.g., a patient's history), in order."""
    return [score_reading(r) for r in readings]
