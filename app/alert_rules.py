"""
Alert rule engine.

This module contains pure detection logic only — given a reading (or a
patient's recent history), decide whether an alert-worthy condition
exists, and if so, describe it. It never touches HTTP, and it never
decides who to notify or whether to suppress a duplicate — that happens
one layer up, in app/alert_crud.py, which is responsible for turning a
"breach detected" result into an actual (possibly deduplicated) Alert row.

Two independent detectors are implemented, matching the assignment scope:

1. evaluate_vital_threshold() — a single-reading check against
   patient-specific or global VitalThreshold rows (Develop Health
   Monitoring Alerts > vital signs threshold monitoring).

2. evaluate_activity_patterns() — a small pattern-detection rule engine
   over a patient's recent ActivityData history (Develop Health
   Monitoring Alerts > activity pattern detection), supporting three rule
   types: sustained_drop, missing_data, and sudden_change.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.alert_models import (
    VitalThreshold, ActivityRule, Severity, AlertSourceType, ActivityRuleType, SEVERITY_RANK,
)


@dataclass
class BreachResult:
    """A single detected alert-worthy condition, ready to be handed to the
    alert management layer (app/alert_crud.py) for dedup + dispatch."""
    source_type: AlertSourceType
    rule_type: str
    severity: Severity
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------
# 1. Vital sign threshold monitoring
# ---------------------------------------------------------------------

def evaluate_vital_threshold(db: Session, vital: models.VitalSigns) -> Optional[BreachResult]:
    """Check one new vital sign reading against the applicable threshold
    rows for this patient and vital_type.

    Patient-specific thresholds (patient_id = this patient) take priority
    over global default thresholds (patient_id IS NULL) — a clinician's
    explicit judgment about *this* patient always overrides the
    population-wide default. If a patient has any active threshold rows
    of their own for this vital_type, the global defaults are ignored
    entirely for that patient/vital_type combination.

    If more than one severity band is breached (e.g. both a "high" band
    and a nested, narrower "critical" band), the single most severe
    breach is returned — we never fire two alerts for one reading.
    """
    value = float(vital.value)

    patient_specific = db.execute(
        select(VitalThreshold).where(
            VitalThreshold.patient_id == str(vital.patient_id),
            VitalThreshold.vital_type == vital.vital_type,
            VitalThreshold.is_active == True,  # noqa: E712
        )
    ).scalars().all()

    thresholds = patient_specific or db.execute(
        select(VitalThreshold).where(
            VitalThreshold.patient_id.is_(None),
            VitalThreshold.vital_type == vital.vital_type,
            VitalThreshold.is_active == True,  # noqa: E712
        )
    ).scalars().all()

    worst_breach: Optional[VitalThreshold] = None
    for t in thresholds:
        lo = float(t.min_value) if t.min_value is not None else float("-inf")
        hi = float(t.max_value) if t.max_value is not None else float("inf")
        breached = value < lo or value > hi
        if breached:
            if worst_breach is None or SEVERITY_RANK[t.severity] > SEVERITY_RANK[worst_breach.severity]:
                worst_breach = t

    if worst_breach is None:
        return None

    lo_txt = str(worst_breach.min_value) if worst_breach.min_value is not None else "no lower bound"
    hi_txt = str(worst_breach.max_value) if worst_breach.max_value is not None else "no upper bound"
    scope = "patient-specific" if patient_specific else "global default"

    return BreachResult(
        source_type=AlertSourceType.vital_sign,
        rule_type=f"vital_out_of_range:{vital.vital_type.value}",
        severity=worst_breach.severity,
        message=(
            f"{vital.vital_type.value.replace('_', ' ')} reading of {value}{vital.unit} is outside "
            f"the {worst_breach.severity.value} range ({lo_txt} to {hi_txt})."
        ),
        details={
            "vital_type": vital.vital_type.value,
            "measured_value": value,
            "unit": vital.unit,
            "threshold_id": str(worst_breach.id),
            "threshold_scope": scope,
            "threshold_min": float(worst_breach.min_value) if worst_breach.min_value is not None else None,
            "threshold_max": float(worst_breach.max_value) if worst_breach.max_value is not None else None,
            "recorded_at": vital.recorded_at.isoformat(),
        },
    )


# ---------------------------------------------------------------------
# 2. Activity pattern detection
# ---------------------------------------------------------------------

def _get_active_rules(db: Session, patient_id: str, activity_type: models.ActivityType) -> List[ActivityRule]:
    patient_specific = db.execute(
        select(ActivityRule).where(
            ActivityRule.patient_id == patient_id,
            ActivityRule.activity_type == activity_type,
            ActivityRule.is_active == True,  # noqa: E712
        )
    ).scalars().all()
    if patient_specific:
        return list(patient_specific)
    return list(db.execute(
        select(ActivityRule).where(
            ActivityRule.patient_id.is_(None),
            ActivityRule.activity_type == activity_type,
            ActivityRule.is_active == True,  # noqa: E712
        )
    ).scalars().all())


def _recent_activity(db: Session, patient_id: str, activity_type: models.ActivityType, days: int, as_of: date):
    start = as_of - timedelta(days=days - 1)
    rows = db.execute(
        select(models.ActivityData).where(
            models.ActivityData.patient_id == patient_id,
            models.ActivityData.activity_type == activity_type,
            models.ActivityData.recorded_date >= start,
            models.ActivityData.recorded_date <= as_of,
        ).order_by(models.ActivityData.recorded_date)
    ).scalars().all()
    return list(rows)


def evaluate_activity_patterns(
    db: Session, patient_id: str, activity_type: models.ActivityType, as_of: Optional[date] = None
) -> List[BreachResult]:
    """Run every active rule (patient-specific if any exist, otherwise the
    global defaults) for one patient/activity_type combination, using data
    up through `as_of` (defaults to today). Returns zero or more breaches
    — unlike the single-reading vital check, more than one activity rule
    can legitimately fire at once (e.g. a sustained drop AND missing data
    are different problems, not competing severities of the same one)."""
    as_of = as_of or datetime.now(timezone.utc).date()
    rules = _get_active_rules(db, patient_id, activity_type)
    results: List[BreachResult] = []

    for rule in rules:
        params = rule.parameters or {}

        if rule.rule_type == ActivityRuleType.sustained_drop:
            lookback_days = int(params.get("lookback_days", 7))
            baseline_days = int(params.get("baseline_days", 14))
            drop_pct = float(params.get("drop_pct", 40))

            recent = _recent_activity(db, patient_id, activity_type, lookback_days, as_of)
            baseline_all = _recent_activity(db, patient_id, activity_type, baseline_days, as_of - timedelta(days=lookback_days))

            if recent and baseline_all:
                recent_avg = sum(float(r.value) for r in recent) / len(recent)
                baseline_avg = sum(float(r.value) for r in baseline_all) / len(baseline_all)
                if baseline_avg > 0:
                    drop = (baseline_avg - recent_avg) / baseline_avg * 100
                    if drop >= drop_pct:
                        results.append(BreachResult(
                            source_type=AlertSourceType.activity,
                            rule_type=f"sustained_drop:{activity_type.value}",
                            severity=rule.severity,
                            message=(
                                f"{activity_type.value.replace('_', ' ')} has dropped {drop:.0f}% over the last "
                                f"{lookback_days} days (avg {recent_avg:.1f}) compared to the prior "
                                f"{baseline_days}-day baseline (avg {baseline_avg:.1f})."
                            ),
                            details={
                                "activity_type": activity_type.value, "recent_avg": round(recent_avg, 2),
                                "baseline_avg": round(baseline_avg, 2), "drop_pct_observed": round(drop, 1),
                                "drop_pct_threshold": drop_pct, "rule_id": str(rule.id),
                            },
                        ))

        elif rule.rule_type == ActivityRuleType.missing_data:
            missing_days = int(params.get("missing_days", 2))
            window = _recent_activity(db, patient_id, activity_type, missing_days, as_of)
            covered_dates = {r.recorded_date for r in window}
            expected_dates = {as_of - timedelta(days=d) for d in range(missing_days)}
            if not (expected_dates & covered_dates):
                results.append(BreachResult(
                    source_type=AlertSourceType.activity,
                    rule_type=f"missing_data:{activity_type.value}",
                    severity=rule.severity,
                    message=(
                        f"No {activity_type.value.replace('_', ' ')} data recorded for the last "
                        f"{missing_days} day(s) (since {min(expected_dates)})."
                    ),
                    details={
                        "activity_type": activity_type.value, "missing_days": missing_days,
                        "as_of": as_of.isoformat(), "rule_id": str(rule.id),
                    },
                ))

        elif rule.rule_type == ActivityRuleType.sudden_change:
            window_days = int(params.get("window_days", 7))
            deviation_pct = float(params.get("deviation_pct", 50))

            history = _recent_activity(db, patient_id, activity_type, window_days + 1, as_of)
            today_rows = [r for r in history if r.recorded_date == as_of]
            prior_rows = [r for r in history if r.recorded_date != as_of]

            if today_rows and prior_rows:
                today_value = float(today_rows[-1].value)
                prior_avg = sum(float(r.value) for r in prior_rows) / len(prior_rows)
                if prior_avg > 0:
                    deviation = abs(today_value - prior_avg) / prior_avg * 100
                    if deviation >= deviation_pct:
                        direction = "above" if today_value > prior_avg else "below"
                        results.append(BreachResult(
                            source_type=AlertSourceType.activity,
                            rule_type=f"sudden_change:{activity_type.value}",
                            severity=rule.severity,
                            message=(
                                f"Today's {activity_type.value.replace('_', ' ')} ({today_value:.1f}) is "
                                f"{deviation:.0f}% {direction} the {window_days}-day average ({prior_avg:.1f})."
                            ),
                            details={
                                "activity_type": activity_type.value, "today_value": today_value,
                                "prior_avg": round(prior_avg, 2), "deviation_pct_observed": round(deviation, 1),
                                "deviation_pct_threshold": deviation_pct, "direction": direction,
                                "rule_id": str(rule.id),
                            },
                        ))

    return results
