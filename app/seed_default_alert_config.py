"""
Seed the global default alert configuration (patient_id = NULL rows).

These are the population-wide defaults that apply to any patient who does
not have their own patient-specific override. Run once per environment:

    python3 -m scripts.seed_default_alert_config

NOTE ON THE ACTUAL NUMBERS: these are simplified, illustrative reference
ranges appropriate for a class capstone project, not verified clinical
guidance. A real deployment would have these ranges reviewed and signed
off by clinical staff before going live (see the Alert Configuration
Document for this caveat and the sourcing note).
"""
from app.database import SessionLocal
from app.models import VitalType, ActivityType
from app.alert_models import VitalThreshold, ActivityRule, Severity, ActivityRuleType


VITAL_DEFAULTS = [
    # vital_type, severity, min, max — bands are nested (critical is the
    # narrowest range, so a critical breach also breaches the wider
    # medium/high bands, and the rule engine reports only the worst one).
    (VitalType.heart_rate, Severity.medium, 50, 120),
    (VitalType.heart_rate, Severity.high, 40, 140),
    (VitalType.heart_rate, Severity.critical, 30, 160),

    (VitalType.blood_pressure_systolic, Severity.medium, 90, 140),
    (VitalType.blood_pressure_systolic, Severity.high, 80, 160),
    (VitalType.blood_pressure_systolic, Severity.critical, 70, 180),

    (VitalType.blood_pressure_diastolic, Severity.medium, 60, 90),
    (VitalType.blood_pressure_diastolic, Severity.high, 50, 100),
    (VitalType.blood_pressure_diastolic, Severity.critical, 40, 110),

    (VitalType.spo2, Severity.medium, 94, 100),
    (VitalType.spo2, Severity.high, 90, 100),
    (VitalType.spo2, Severity.critical, 85, 100),

    (VitalType.blood_glucose, Severity.medium, 70, 180),
    (VitalType.blood_glucose, Severity.high, 54, 250),
    (VitalType.blood_glucose, Severity.critical, 40, 400),

    (VitalType.temperature, Severity.medium, 36.1, 37.8),
    (VitalType.temperature, Severity.high, 35.0, 39.0),
    (VitalType.temperature, Severity.critical, 34.0, 40.0),
]

ACTIVITY_RULE_DEFAULTS = [
    # activity_type, rule_type, severity, parameters
    (ActivityType.steps, ActivityRuleType.sustained_drop, Severity.medium,
     {"lookback_days": 7, "baseline_days": 14, "drop_pct": 40}),
    (ActivityType.steps, ActivityRuleType.missing_data, Severity.medium,
     {"missing_days": 2}),

    (ActivityType.active_minutes, ActivityRuleType.sustained_drop, Severity.medium,
     {"lookback_days": 7, "baseline_days": 14, "drop_pct": 50}),
    (ActivityType.active_minutes, ActivityRuleType.missing_data, Severity.low,
     {"missing_days": 3}),

    (ActivityType.sleep_hours, ActivityRuleType.sudden_change, Severity.low,
     {"window_days": 7, "deviation_pct": 40}),
    (ActivityType.sleep_hours, ActivityRuleType.missing_data, Severity.low,
     {"missing_days": 3}),

    (ActivityType.calories_burned, ActivityRuleType.sustained_drop, Severity.low,
     {"lookback_days": 7, "baseline_days": 14, "drop_pct": 40}),

    (ActivityType.distance_km, ActivityRuleType.sustained_drop, Severity.low,
     {"lookback_days": 7, "baseline_days": 14, "drop_pct": 50}),
]


def seed():
    db = SessionLocal()
    try:
        existing = db.query(VitalThreshold).filter(VitalThreshold.patient_id.is_(None)).count()
        if existing:
            print(f"Global vital thresholds already seeded ({existing} rows found) — skipping.")
        else:
            for vital_type, severity, lo, hi in VITAL_DEFAULTS:
                db.add(VitalThreshold(
                    patient_id=None, vital_type=vital_type, severity=severity,
                    min_value=lo, max_value=hi, is_active=True,
                ))
            db.commit()
            print(f"Seeded {len(VITAL_DEFAULTS)} global vital threshold rows.")

        existing_rules = db.query(ActivityRule).filter(ActivityRule.patient_id.is_(None)).count()
        if existing_rules:
            print(f"Global activity rules already seeded ({existing_rules} rows found) — skipping.")
        else:
            for activity_type, rule_type, severity, params in ACTIVITY_RULE_DEFAULTS:
                db.add(ActivityRule(
                    patient_id=None, activity_type=activity_type, rule_type=rule_type,
                    severity=severity, parameters=params, is_active=True,
                ))
            db.commit()
            print(f"Seeded {len(ACTIVITY_RULE_DEFAULTS)} global activity rule rows.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
