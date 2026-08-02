"""
Synthetic patient vitals generator for HealthTrack Risk Assessment.

IMPORTANT: This produces SYNTHETIC data only. No real patient data is used
anywhere in this system. The generator encodes simplified physiological
relationships (e.g., deteriorating patients trend toward tachycardia,
hypotension, low SpO2, fever/hypothermia, tachypnea) so that the resulting
dataset is realistic enough to sanity-check the scoring, pattern-recognition
and predictive components -- it is NOT a substitute for clinically validated
training data and must be replaced with real, IRB-approved, de-identified
patient data before any real-world clinical use.
"""

import numpy as np
import pandas as pd


RNG_SEED = 42


def _clip(v, lo, hi):
    return float(np.clip(v, lo, hi))


def generate_patient_cohort(n_patients=300, days=14, readings_per_day=4, seed=RNG_SEED):
    """
    Generates a synthetic longitudinal cohort of vitals readings.

    Each patient has:
      - demographics / comorbidities (age, diabetes, hypertension, copd, heart_disease)
      - a stable baseline for each vital
      - ~22% chance of a "deterioration trajectory" starting at a random point
        in the record, which drifts vitals toward a labeled adverse event
      - an `adverse_event_72h` label per reading: whether a clinically
        significant deterioration event occurs within the next 72 hours
        (this is the prediction target for the supervised model)

    Returns a tidy DataFrame, one row per reading.
    """
    rng = np.random.default_rng(seed)
    rows = []

    for pid in range(1, n_patients + 1):
        age = int(_clip(rng.normal(62, 16), 18, 95))
        diabetes = rng.random() < 0.28
        hypertension = rng.random() < 0.38
        copd = rng.random() < 0.12
        heart_disease = rng.random() < 0.20

        # baselines, mildly shifted by comorbidities (kept simple/synthetic)
        base_hr = rng.normal(72, 8) + (5 if heart_disease else 0)
        base_sbp = rng.normal(122, 12) + (10 if hypertension else 0)
        base_dbp = rng.normal(78, 8) + (5 if hypertension else 0)
        base_spo2 = rng.normal(97.5, 1.0) - (2.0 if copd else 0)
        base_temp = rng.normal(36.8, 0.3)
        base_rr = rng.normal(16, 2) + (2 if copd else 0)
        base_glucose = rng.normal(100, 15) + (30 if diabetes else 0)

        n_readings = days * readings_per_day
        deteriorates = rng.random() < 0.22
        onset_idx = rng.integers(low=int(n_readings * 0.3), high=n_readings) if deteriorates else None

        patient_rows = []
        for i in range(n_readings):
            t_hours = i * (24 / readings_per_day)

            drift = 0.0
            if deteriorates and onset_idx is not None and i >= onset_idx:
                # progressive drift over ~48-72h toward decompensation
                steps_since_onset = i - onset_idx
                drift = min(1.0, steps_since_onset / (12 if readings_per_day >= 4 else 6))

            noise = rng.normal(0, 1, size=7)

            hr = base_hr + drift * rng.uniform(25, 45) + noise[0] * 3
            sbp = base_sbp - drift * rng.uniform(15, 35) + noise[1] * 5
            dbp = base_dbp - drift * rng.uniform(8, 18) + noise[2] * 4
            spo2 = base_spo2 - drift * rng.uniform(4, 10) + noise[3] * 0.6
            temp = base_temp + drift * rng.choice([1, -1]) * rng.uniform(1.0, 2.5) + noise[4] * 0.15
            rr = base_rr + drift * rng.uniform(6, 14) + noise[5] * 1.2
            glucose = base_glucose + drift * rng.uniform(-40, 60) + noise[6] * 8

            row = {
                "patient_id": pid,
                "age": age,
                "diabetes": diabetes,
                "hypertension": hypertension,
                "copd": copd,
                "heart_disease": heart_disease,
                "hours_elapsed": t_hours,
                "reading_index": i,
                "heart_rate": _clip(hr, 30, 200),
                "systolic_bp": _clip(sbp, 60, 220),
                "diastolic_bp": _clip(dbp, 35, 140),
                "spo2": _clip(spo2, 55, 100),
                "temperature_c": _clip(temp, 32.0, 41.5),
                "respiratory_rate": _clip(rr, 6, 45),
                "blood_glucose": _clip(glucose, 30, 450),
            }
            patient_rows.append(row)

        # label: adverse event occurs within 72h window looking forward
        readings_per_72h = int(72 / (24 / readings_per_day))
        event_idx = onset_idx + readings_per_72h if (deteriorates and onset_idx is not None) else None
        # the "event" is placed a bit after onset once drift is severe enough
        if deteriorates and onset_idx is not None:
            severity = np.array([
                1 if (i >= onset_idx and (i - onset_idx) / (12 if readings_per_day >= 4 else 6) >= 0.75) else 0
                for i in range(n_readings)
            ])
            first_severe = np.argmax(severity) if severity.any() else None
            event_idx = int(first_severe) if severity.any() else None

        for i, row in enumerate(patient_rows):
            if event_idx is not None:
                horizon_end = i + readings_per_72h
                label = 1 if (event_idx >= i and event_idx <= horizon_end) else 0
            else:
                label = 0
            row["adverse_event_72h"] = label
            rows.append(row)

    df = pd.DataFrame(rows)
    return df


def generate_scenario_patients():
    """
    Hand-crafted edge-case scenarios used for scenario-based testing
    (not random) -- see tests/test_scenarios.py.
    """
    scenarios = {}

    scenarios["stable_healthy_adult"] = pd.DataFrame([
        {"heart_rate": 70, "systolic_bp": 118, "diastolic_bp": 76, "spo2": 98,
         "temperature_c": 36.7, "respiratory_rate": 15, "blood_glucose": 95}
        for _ in range(6)
    ])

    scenarios["hypoxia_copd_patient"] = pd.DataFrame([
        {"heart_rate": 95, "systolic_bp": 130, "diastolic_bp": 82, "spo2": 88,
         "temperature_c": 37.0, "respiratory_rate": 26, "blood_glucose": 105}
        for _ in range(6)
    ])

    scenarios["sepsis_pattern"] = pd.DataFrame([
        {"heart_rate": 118, "systolic_bp": 88, "diastolic_bp": 55, "spo2": 91,
         "temperature_c": 38.9, "respiratory_rate": 24, "blood_glucose": 140}
        for _ in range(6)
    ])

    scenarios["hypertensive_crisis"] = pd.DataFrame([
        {"heart_rate": 88, "systolic_bp": 192, "diastolic_bp": 118, "spo2": 97,
         "temperature_c": 36.9, "respiratory_rate": 18, "blood_glucose": 110}
        for _ in range(6)
    ])

    scenarios["hypoglycemia_diabetic"] = pd.DataFrame([
        {"heart_rate": 102, "systolic_bp": 105, "diastolic_bp": 68, "spo2": 97,
         "temperature_c": 36.5, "respiratory_rate": 17, "blood_glucose": 48}
        for _ in range(6)
    ])

    # gradually worsening trend over 6 readings (for trend-analysis tests)
    worsening = []
    for i in range(6):
        worsening.append({
            "heart_rate": 74 + i * 7,
            "systolic_bp": 120 - i * 6,
            "diastolic_bp": 78 - i * 3,
            "spo2": 98 - i * 1.5,
            "temperature_c": 36.8 + i * 0.15,
            "respiratory_rate": 15 + i * 1.8,
            "blood_glucose": 100 + i * 4,
        })
    scenarios["worsening_trend"] = pd.DataFrame(worsening)

    # missing / partial data
    scenarios["missing_data"] = pd.DataFrame([
        {"heart_rate": 80, "systolic_bp": None, "diastolic_bp": 78, "spo2": 96,
         "temperature_c": None, "respiratory_rate": 16, "blood_glucose": 98}
    ])

    return scenarios


if __name__ == "__main__":
    df = generate_patient_cohort()
    df.to_csv("/home/claude/healthtrack_risk/data/synthetic_cohort.csv", index=False)
    print(df.shape)
    print(df["adverse_event_72h"].value_counts())
