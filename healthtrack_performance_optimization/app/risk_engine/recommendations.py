"""
Recommendation Generation
----------------------------
Generates automated CARE-TEAM-FACING suggestions from identified risk
factors and the overall risk level. These are deliberately:
  - Rule-based and traceable to a specific factor (no opaque generation)
  - Labeled everywhere as system-generated suggestions, not orders
  - Framed as prompts for clinical judgement, not replacements for it

This module NEVER outputs a diagnosis or a directive ("must", "diagnose",
"treat with X drug"); it recommends a review action or monitoring change,
consistent with a clinical decision-SUPPORT tool rather than an autonomous
decision-making one.
"""

DISCLAIMER = (
    "SYSTEM-GENERATED SUGGESTION -- for clinical decision support only. "
    "Not a diagnosis and not a substitute for clinical judgement. "
    "A qualified healthcare provider must review and confirm any action."
)

RULES = [
    {
        "condition": lambda f: any(cf["metric"] == "spo2" and cf["points"] >= 2 for cf in f["contributing_factors"]),
        "recommendation": "Consider supplemental oxygen assessment and repeat SpO2 reading; verify sensor placement.",
        "basis": "spo2",
    },
    {
        "condition": lambda f: any(cf["metric"] == "respiratory_rate" and cf["points"] >= 2 for cf in f["contributing_factors"]),
        "recommendation": "Recommend clinical assessment of respiratory status and work of breathing.",
        "basis": "respiratory_rate",
    },
    {
        "condition": lambda f: any(cf["metric"] == "heart_rate" and cf["points"] >= 2 for cf in f["contributing_factors"]),
        "recommendation": "Recommend ECG/cardiac review given significant heart-rate abnormality.",
        "basis": "heart_rate",
    },
    {
        "condition": lambda f: any(cf["metric"] == "systolic_bp" and cf["band"] in ("critically low", "low") for cf in f["contributing_factors"]),
        "recommendation": "Recommend fluid status and hemodynamic assessment given low blood pressure.",
        "basis": "systolic_bp_low",
    },
    {
        "condition": lambda f: any(cf["metric"] == "systolic_bp" and cf["band"] == "hypertensive crisis" for cf in f["contributing_factors"])
                    or any(cf["metric"] == "diastolic_bp" and cf["band"] == "hypertensive crisis" for cf in f["contributing_factors"]),
        "recommendation": "Recommend urgent blood pressure recheck and hypertensive-crisis protocol evaluation.",
        "basis": "hypertensive_crisis",
    },
    {
        "condition": lambda f: any(cf["metric"] == "temperature_c" and cf["points"] >= 1 for cf in f["contributing_factors"]),
        "recommendation": "Recommend infection/thermoregulation workup if temperature trend persists.",
        "basis": "temperature",
    },
    {
        "condition": lambda f: any(cf["metric"] == "blood_glucose" and cf["band"] in ("severe hypoglycemia", "hypoglycemia") for cf in f["contributing_factors"]),
        "recommendation": "Recommend immediate glucose recheck and hypoglycemia protocol given low blood glucose.",
        "basis": "hypoglycemia",
    },
    {
        "condition": lambda f: any(cf["metric"] == "blood_glucose" and cf["band"] in ("severe hyperglycemia", "hyperglycemia") for cf in f["contributing_factors"]),
        "recommendation": "Recommend glucose recheck and diabetes management review given elevated blood glucose.",
        "basis": "hyperglycemia",
    },
    {
        "condition": lambda f: len(f["compound_patterns"]) > 0,
        "recommendation": "Multiple abnormal vitals cluster into a recognized pattern -- recommend prioritized clinical review rather than addressing each vital in isolation.",
        "basis": "compound_pattern",
    },
    {
        "condition": lambda f: len(f["baseline_deviations"]) > 0,
        "recommendation": "One or more readings are unusual for this patient specifically (relative to their own history), even though absolute values may appear acceptable -- recommend clinician review with the patient's trend chart.",
        "basis": "personal_baseline",
    },
]


def generate_recommendations(risk_factor_result: dict, risk_level_label: str, trend_direction: str = None) -> dict:
    """
    risk_factor_result: output of risk_factors.identify_risk_factors
    risk_level_label: "Low" | "Moderate" | "High" | "Critical"
    trend_direction: optional "worsening" | "stable" | "improving"
    """
    recs = []
    for rule in RULES:
        try:
            if rule["condition"](risk_factor_result):
                recs.append({"recommendation": rule["recommendation"], "basis": rule["basis"]})
        except Exception:
            continue

    if risk_level_label == "Critical":
        recs.insert(0, {"recommendation": "Escalate for immediate clinical response.", "basis": "risk_level_critical"})
    elif risk_level_label == "High":
        recs.insert(0, {"recommendation": "Schedule urgent clinical review within the hour.", "basis": "risk_level_high"})

    if trend_direction == "worsening":
        recs.append({"recommendation": "Risk trend is worsening over recent readings -- recommend increasing monitoring frequency even if the current single reading is not yet severe.", "basis": "trend_worsening"})

    if not recs:
        recs.append({"recommendation": "No specific risk factors identified; continue routine monitoring schedule.", "basis": "routine"})

    return {
        "disclaimer": DISCLAIMER,
        "risk_level": risk_level_label,
        "recommendations": recs,
    }
