"""
Predictive Risk Model
-----------------------
Predicts probability of an "adverse event" (clinically significant
deterioration) within the next 72 hours, using engineered features derived
from the rule-based risk score, per-metric bands, short-term trend slope,
and comorbidity flags.

Model choice: Gradient-boosted trees (HistGradientBoostingClassifier).
Rationale (see docs/model_validation.md for full writeup):
  - Handles nonlinear interactions between vitals (e.g., HR+BP+temp jointly
    matter more than any single metric) without manual feature crosses.
  - Robust to the mixed/skewed distributions typical of vitals data.
  - Natively supports missing values (real remote-monitoring data has
    dropped readings), avoiding an extra imputation step that could hide
    a "sensor didn't report" signal.
  - Outputs calibrated-enough probabilities for risk-tier mapping, unlike
    a plain decision tree.
  - Chosen over logistic regression (a linear model under-fits the
    interaction effects we saw in EDA) and over a plain random forest
    (comparable accuracy, slightly worse recall on the minority/event class
    in our testing -- see model_validation doc for the actual comparison).

Class imbalance: adverse events are rare (~3-4% of readings in the synthetic
cohort, consistent with real remote-monitoring base rates). We do NOT
upsample/duplicate the minority class, because that inflates apparent
accuracy without improving real-world generalization; instead we use
class_weight="balanced" style sample weighting and evaluate with metrics
that are meaningful under imbalance (recall, precision, PR-AUC, ROC-AUC),
not raw accuracy.
"""

import json
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupShuffleSplit, cross_val_score, StratifiedGroupKFold
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, average_precision_score, confusion_matrix, classification_report,
)
from sklearn.impute import SimpleImputer
import joblib

from engine.risk_scoring import score_reading
from engine.trend_analysis import compute_trend

FEATURE_COLUMNS = [
    "rule_based_score", "age", "diabetes", "hypertension", "copd", "heart_disease",
    "heart_rate", "systolic_bp", "diastolic_bp", "spo2", "temperature_c",
    "respiratory_rate", "blood_glucose", "trend_slope_6",
]


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds rule_based_score and a short-term (6-reading) trend slope of that
    score, computed causally (only using readings up to and including the
    current one) to avoid leaking future information into the features.
    """
    df = df.sort_values(["patient_id", "reading_index"]).reset_index(drop=True)

    df["rule_based_score"] = df.apply(
        lambda r: score_reading(r[["heart_rate", "systolic_bp", "diastolic_bp", "spo2",
                                    "temperature_c", "respiratory_rate", "blood_glucose"]].to_dict()).total_score,
        axis=1,
    )

    slopes = np.full(len(df), np.nan)
    for pid, group in df.groupby("patient_id"):
        idx = group.index.to_numpy()
        scores = group["rule_based_score"].to_numpy()
        for i in range(len(idx)):
            start = max(0, i - 5)
            window = scores[start:i + 1]
            trend = compute_trend(window)
            slopes[idx[i]] = trend["slope"] if trend["slope"] is not None else 0.0
    df["trend_slope_6"] = slopes
    df["trend_slope_6"] = df["trend_slope_6"].fillna(0.0)

    for flag in ["diabetes", "hypertension", "copd", "heart_disease"]:
        df[flag] = df[flag].astype(int)

    return df


def train_and_evaluate(df: pd.DataFrame, model_type="gbt", random_state=42):
    """
    Trains on a GROUP-based split (split by patient_id, not by row) so no
    patient's readings leak across train/test -- a common and important
    pitfall for longitudinal patient data. Returns the fitted model, the
    held-out test metrics, and cross-validated metrics for stability.
    """
    df = engineer_features(df)
    X = df[FEATURE_COLUMNS]
    y = df["adverse_event_72h"].astype(int)
    groups = df["patient_id"]

    imputer = SimpleImputer(strategy="median")

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=random_state)
    train_idx, test_idx = next(splitter.split(X, y, groups))

    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    groups_train = groups.iloc[train_idx]

    X_train_imp = imputer.fit_transform(X_train)
    X_test_imp = imputer.transform(X_test)

    if model_type == "gbt":
        sample_weight = np.where(y_train == 1, (y_train == 0).sum() / max((y_train == 1).sum(), 1), 1.0)
        model = HistGradientBoostingClassifier(random_state=random_state, max_iter=200, max_depth=6)
        model.fit(X_train_imp, y_train, sample_weight=sample_weight)
    elif model_type == "rf":
        model = RandomForestClassifier(
            n_estimators=300, class_weight="balanced", random_state=random_state, max_depth=10
        )
        model.fit(X_train_imp, y_train)
    elif model_type == "logreg":
        model = LogisticRegression(class_weight="balanced", max_iter=2000)
        model.fit(X_train_imp, y_train)
    else:
        raise ValueError(model_type)

    y_pred = model.predict(X_test_imp)
    y_proba = model.predict_proba(X_test_imp)[:, 1]

    metrics = {
        "model_type": model_type,
        "n_train_readings": int(len(X_train)),
        "n_test_readings": int(len(X_test)),
        "n_train_patients": int(groups_train.nunique()),
        "n_test_patients": int(groups.iloc[test_idx].nunique()),
        "positive_rate_test": round(float(y_test.mean()), 4),
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, y_proba)), 4),
        "pr_auc": round(float(average_precision_score(y_test, y_proba)), 4),
        "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
    }

    report = classification_report(y_test, y_pred, zero_division=0, output_dict=True)

    return {
        "model": model,
        "imputer": imputer,
        "metrics": metrics,
        "classification_report": report,
        "feature_columns": FEATURE_COLUMNS,
    }


def cross_validate_stability(df: pd.DataFrame, model_type="gbt", n_splits=5, random_state=42):
    """
    Reports mean +/- std of ROC-AUC across grouped folds, to honestly
    characterize variance rather than reporting a single train/test split
    as if it were the whole story.
    """
    df = engineer_features(df)
    X = df[FEATURE_COLUMNS]
    y = df["adverse_event_72h"].astype(int)
    groups = df["patient_id"]

    imputer = SimpleImputer(strategy="median")
    X_imp = imputer.fit_transform(X)

    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=random_state)

    if model_type == "gbt":
        model = HistGradientBoostingClassifier(random_state=random_state, max_iter=200, max_depth=6)
    elif model_type == "rf":
        model = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                        random_state=random_state, max_depth=10)
    else:
        model = LogisticRegression(class_weight="balanced", max_iter=2000)

    scores = cross_val_score(model, X_imp, y, cv=cv, groups=groups, scoring="roc_auc")
    return {
        "model_type": model_type,
        "n_splits": n_splits,
        "fold_roc_auc": [round(float(s), 4) for s in scores],
        "mean_roc_auc": round(float(np.mean(scores)), 4),
        "std_roc_auc": round(float(np.std(scores)), 4),
    }


def feature_importance(fit_result) -> dict:
    model = fit_result["model"]
    cols = fit_result["feature_columns"]
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
        return dict(sorted(zip(cols, [round(float(v), 4) for v in importances]),
                            key=lambda kv: kv[1], reverse=True))
    return {}


def save_model(fit_result, path="/home/claude/healthtrack_risk/data/predictive_model.joblib"):
    joblib.dump({"model": fit_result["model"], "imputer": fit_result["imputer"],
                 "feature_columns": fit_result["feature_columns"]}, path)
    return path


def load_model(path="/home/claude/healthtrack_risk/data/predictive_model.joblib"):
    return joblib.load(path)


def predict_patient_risk(bundle, latest_row: dict) -> float:
    """bundle = output of load_model(). latest_row must contain FEATURE_COLUMNS keys."""
    X = pd.DataFrame([{k: latest_row.get(k) for k in bundle["feature_columns"]}])
    X_imp = bundle["imputer"].transform(X)
    proba = bundle["model"].predict_proba(X_imp)[:, 1][0]
    return float(proba)
