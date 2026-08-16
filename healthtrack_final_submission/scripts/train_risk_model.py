"""
Trains the Random Forest risk model used by app.risk_engine and saves it
to app/risk_model.joblib. Uses a synthetic vitals dataset with the same
clinically-inspired generation approach used in earlier deliverables.

Run: python scripts/train_risk_model.py
"""
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, f1_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def generate_dataset(n=2000, seed=42):
    rng = np.random.default_rng(seed)
    heart_rate = rng.normal(78, 15, n).clip(40, 180)
    systolic_bp = rng.normal(122, 18, n).clip(80, 200)
    diastolic_bp = rng.normal(78, 12, n).clip(50, 130)
    spo2 = rng.normal(96.5, 2.5, n).clip(80, 100)
    temperature = rng.normal(98.4, 1.0, n).clip(95, 104)
    resp_rate = rng.normal(16, 4, n).clip(8, 40)

    risk_score = (
        (heart_rate > 100).astype(int) * 1.5 +
        (heart_rate < 55).astype(int) * 1.0 +
        (spo2 < 94).astype(int) * 2.0 +
        (systolic_bp > 150).astype(int) * 1.5 +
        (diastolic_bp > 95).astype(int) * 1.0 +
        (temperature > 100.4).astype(int) * 1.5 +
        (resp_rate > 22).astype(int) * 1.0 +
        rng.normal(0, 0.8, n)
    )
    risk_label = np.where(risk_score >= 2.0, "High", "Low")

    return pd.DataFrame({
        "heart_rate": heart_rate, "systolic_bp": systolic_bp,
        "diastolic_bp": diastolic_bp, "spo2": spo2,
        "temperature": temperature, "resp_rate": resp_rate,
        "risk_label": risk_label
    })


def main():
    df = generate_dataset()
    X = df[["heart_rate", "systolic_bp", "diastolic_bp", "spo2", "temperature", "resp_rate"]]
    y = df["risk_label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = RandomForestClassifier(
        n_estimators=200, max_depth=8, min_samples_leaf=2, random_state=42
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    print("Classes:", model.classes_)
    print("Test accuracy:", round(accuracy_score(y_test, pred), 3))
    print("Test F1 (High):", round(f1_score(y_test, pred, pos_label="High"), 3))
    print(classification_report(y_test, pred))

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "risk_model.joblib")
    joblib.dump(model, out_path)
    print("Saved model to", out_path)


if __name__ == "__main__":
    main()
