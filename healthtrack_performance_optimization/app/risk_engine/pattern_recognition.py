"""
Pattern Recognition Module
---------------------------
Detects abnormal data patterns in two complementary ways:

1. Personal-baseline deviation: flags readings that are statistically
   unusual FOR THIS PATIENT, using a rolling mean/std of their own recent
   history (z-score). This catches "relative" deterioration even when a
   value is still inside a generic normal range (e.g., a patient whose
   resting HR is normally 58 jumping to 95 is a meaningful change even
   though 95 is "normal" on a population chart).

2. Population-outlier detection: flags readings that are statistically
   unusual relative to a broader reference population, using robust
   z-scores (median/MAD) to reduce sensitivity to outliers in the
   reference set itself.

Both are intentionally simple, explainable statistical methods rather than
an opaque anomaly model, because a bedside/remote-monitoring alert needs to
be justifiable to a clinician reviewing it.
"""

import numpy as np
import pandas as pd


PERSONAL_Z_ALERT = 2.0       # |z| beyond this vs. patient's own baseline
POPULATION_Z_ALERT = 3.0     # |robust z| beyond this vs. reference population
MIN_HISTORY_FOR_BASELINE = 5


def personal_baseline_anomalies(patient_history: pd.DataFrame, metrics: list,
                                 window: int = 10) -> pd.DataFrame:
    """
    patient_history: DataFrame ordered by time, one row per reading, for a
                      SINGLE patient.
    Returns a DataFrame of the same length with `<metric>_zscore` and
    `<metric>_anomaly` columns added (bool), using a trailing rolling window
    that EXCLUDES the current reading (so we don't compare a value to itself).
    """
    df = patient_history.copy().reset_index(drop=True)

    for metric in metrics:
        if metric not in df.columns:
            continue
        rolling_mean = df[metric].shift(1).rolling(window=window, min_periods=MIN_HISTORY_FOR_BASELINE).mean()
        rolling_std = df[metric].shift(1).rolling(window=window, min_periods=MIN_HISTORY_FOR_BASELINE).std()

        z = (df[metric] - rolling_mean) / rolling_std.replace(0, np.nan)
        df[f"{metric}_zscore"] = z
        df[f"{metric}_anomaly"] = z.abs() >= PERSONAL_Z_ALERT

    return df


def population_outliers(reading: dict, reference_stats: dict) -> dict:
    """
    reading: single reading dict {metric: value}
    reference_stats: {metric: {"median": ..., "mad": ...}} precomputed over
                      a reference population (see compute_reference_stats)
    Returns {metric: {"robust_z": float, "is_outlier": bool}}
    """
    result = {}
    for metric, value in reading.items():
        if value is None or metric not in reference_stats:
            continue
        stats = reference_stats[metric]
        mad = stats["mad"] if stats["mad"] > 0 else 1e-6
        # 0.6745 scales MAD to be comparable to a standard deviation under normality
        robust_z = 0.6745 * (value - stats["median"]) / mad
        result[metric] = {
            "robust_z": round(float(robust_z), 2),
            "is_outlier": bool(abs(robust_z) >= POPULATION_Z_ALERT),
        }
    return result


def compute_reference_stats(cohort_df: pd.DataFrame, metrics: list) -> dict:
    """Precompute median/MAD per metric across a reference population/cohort."""
    stats = {}
    for metric in metrics:
        if metric not in cohort_df.columns:
            continue
        series = cohort_df[metric].dropna()
        median = series.median()
        mad = (series - median).abs().median()
        stats[metric] = {"median": float(median), "mad": float(mad)}
    return stats


def detect_multi_metric_pattern(latest_scores: dict) -> list:
    """
    Rule-based compound-pattern detection: certain COMBINATIONS of abnormal
    metrics are clinically more significant than any single metric alone.
    latest_scores: {metric: points} from risk_scoring.score_reading
    Returns a list of pattern-name strings that matched.
    """
    patterns = []
    hr = latest_scores.get("heart_rate", 0)
    sbp = latest_scores.get("systolic_bp", 0)
    spo2 = latest_scores.get("spo2", 0)
    temp = latest_scores.get("temperature_c", 0)
    rr = latest_scores.get("respiratory_rate", 0)
    glu = latest_scores.get("blood_glucose", 0)

    if hr >= 2 and sbp >= 2 and temp >= 1:
        patterns.append("possible_sepsis_pattern")
    if spo2 >= 2 and rr >= 2:
        patterns.append("possible_respiratory_failure")
    if sbp >= 2 and hr >= 1 and glu == 0:
        patterns.append("possible_hemodynamic_instability")
    if glu >= 2 and hr >= 1:
        patterns.append("possible_glycemic_crisis")
    if sbp >= 3 and rr == 0 and spo2 == 0:
        patterns.append("possible_hypertensive_crisis")

    return patterns
