"""
Risk Trend Analysis
--------------------
Analyzes how a patient's risk score is moving over their accumulated
history: direction (improving/stable/worsening), rate of change (slope),
and volatility. Used both for the reporting layer (charts) and to feed a
"trending worse" signal into the predictive model.
"""

import numpy as np
import pandas as pd


def compute_trend(risk_scores: list, timestamps: list = None, min_points: int = 3):
    """
    risk_scores: list/array of numeric risk scores (chronological order)
    timestamps: optional list of hours-elapsed (or datetime) matching risk_scores;
                if omitted, index position is used as the x-axis.

    Returns dict with slope (points per reading, or per hour if timestamps given),
    direction label, volatility (std of residuals), and the raw linear fit.
    """
    scores = np.asarray(risk_scores, dtype=float)
    n = len(scores)

    if n < min_points:
        return {
            "n_points": n,
            "slope": None,
            "direction": "insufficient_data",
            "volatility": None,
            "r_squared": None,
        }

    if timestamps is not None:
        x = np.asarray(timestamps, dtype=float)
    else:
        x = np.arange(n, dtype=float)

    # simple linear regression (least squares)
    x_mean, y_mean = x.mean(), scores.mean()
    denom = ((x - x_mean) ** 2).sum()
    slope = float(((x - x_mean) * (scores - y_mean)).sum() / denom) if denom != 0 else 0.0
    intercept = y_mean - slope * x_mean

    predicted = slope * x + intercept
    residuals = scores - predicted
    volatility = float(np.std(residuals))

    ss_res = float((residuals ** 2).sum())
    ss_tot = float(((scores - y_mean) ** 2).sum())
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # direction thresholds are intentionally conservative to avoid flagging
    # normal noise as a trend
    if slope > 0.15:
        direction = "worsening"
    elif slope < -0.15:
        direction = "improving"
    else:
        direction = "stable"

    return {
        "n_points": n,
        "slope": round(slope, 4),
        "direction": direction,
        "volatility": round(volatility, 3),
        "r_squared": round(r_squared, 3),
        "latest_score": float(scores[-1]),
        "mean_score": round(float(y_mean), 2),
    }


def rolling_trend_series(df: pd.DataFrame, score_col: str, window: int = 6) -> pd.DataFrame:
    """
    Computes a rolling trend direction at each point in a patient's history
    so a chart can show "when did this patient start trending worse."
    """
    out = df.copy().reset_index(drop=True)
    directions = []
    slopes = []
    for i in range(len(out)):
        start = max(0, i - window + 1)
        window_scores = out[score_col].iloc[start:i + 1].values
        trend = compute_trend(window_scores)
        directions.append(trend["direction"])
        slopes.append(trend["slope"])
    out["trend_direction"] = directions
    out["trend_slope"] = slopes
    return out
