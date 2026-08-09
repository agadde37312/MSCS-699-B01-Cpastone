"""
Risk Level Classification
---------------------------
Maps a numeric aggregate risk score (0-21, from risk_scoring's 7 metrics x
0-3 points each) plus optional predictive-model probability into a
discrete, clinically actionable risk level with an associated color code
and recommended response urgency.

The banding below is intentionally modeled after the NEWS2 escalation
protocol structure (single very-abnormal parameter triggers escalation
regardless of total score) because that "red flag overrides total" behavior
is clinically important: a single critically abnormal vital should not be
diluted by otherwise-normal readings into a falsely reassuring total.
"""

from dataclasses import dataclass


@dataclass
class RiskLevel:
    label: str
    color: str
    color_hex: str
    urgency: str
    score_range: str


LEVELS = [
    RiskLevel("Low", "green", "#2E7D32", "Routine monitoring", "0-2"),
    RiskLevel("Moderate", "yellow", "#F9A825", "Increased monitoring frequency; clinical review within 24h", "3-6"),
    RiskLevel("High", "orange", "#EF6C00", "Urgent clinical review within 1h", "7-11"),
    RiskLevel("Critical", "red", "#C62828", "Immediate clinical response required", "12+"),
]


def classify(total_score: int, single_metric_scores: list = None,
             model_probability: float = None) -> RiskLevel:
    """
    total_score: aggregate score from risk_scoring.ScoringResult.total_score
    single_metric_scores: list of per-metric point values (0-3); if any
        single metric scores the maximum (3), this is a "red flag" that
        escalates classification to at least High, regardless of total,
        mirroring NEWS2's single-parameter escalation rule.
    model_probability: optional predicted probability of an adverse event
        from the predictive model (0-1); if very high, also escalates.
    """
    red_flag = bool(single_metric_scores) and any(s >= 3 for s in single_metric_scores)

    if total_score >= 12:
        level = LEVELS[3]
    elif total_score >= 7:
        level = LEVELS[2]
    elif total_score >= 3:
        level = LEVELS[1]
    else:
        level = LEVELS[0]

    if red_flag and level.label in ("Low", "Moderate"):
        level = LEVELS[2]  # escalate to High on a single critical parameter

    if model_probability is not None and model_probability >= 0.5 and level.label != "Critical":
        level = LEVELS[3] if model_probability >= 0.75 else max(level, LEVELS[2], key=lambda l: LEVELS.index(l))

    return level


def classify_dict(total_score: int, single_metric_scores: list = None,
                   model_probability: float = None) -> dict:
    level = classify(total_score, single_metric_scores, model_probability)
    return {
        "label": level.label,
        "color": level.color,
        "color_hex": level.color_hex,
        "urgency": level.urgency,
        "score_range": level.score_range,
    }
