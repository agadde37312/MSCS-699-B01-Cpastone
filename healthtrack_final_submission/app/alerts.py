"""
Alert management: creates alerts from risk assessments, deduplicates
repeat alerts for the same patient/condition within a cooldown window,
and escalates alerts that remain unacknowledged past a time threshold.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Alert, AlertSeverity, AlertStatus, RiskLevel, RiskAssessment

DEDUP_COOLDOWN_MINUTES = 15
ESCALATION_MINUTES = 10

SEVERITY_BY_RISK = {
    RiskLevel.LOW: None,               # no alert needed
    RiskLevel.MODERATE: AlertSeverity.WARNING,
    RiskLevel.HIGH: AlertSeverity.WARNING,
    RiskLevel.CRITICAL: AlertSeverity.CRITICAL,
}


def _dedup_key(patient_id: int, risk_level: RiskLevel) -> str:
    return f"patient:{patient_id}:risk:{risk_level.value}"


def create_alert_from_assessment(
    db: Session, patient_id: int, assessment: RiskAssessment
) -> Optional[Alert]:
    """Create an alert for a risk assessment, unless:
    - risk level doesn't warrant an alert (Low), or
    - an open alert with the same dedup key was created within the
      cooldown window (avoids flooding staff with repeat alerts for an
      already-known, already-open condition).
    """
    severity = SEVERITY_BY_RISK.get(assessment.risk_level)
    if severity is None:
        return None

    key = _dedup_key(patient_id, assessment.risk_level)
    cutoff = datetime.utcnow() - timedelta(minutes=DEDUP_COOLDOWN_MINUTES)

    recent_duplicate = (
        db.query(Alert)
        .filter(
            Alert.dedup_key == key,
            Alert.status.in_([AlertStatus.OPEN, AlertStatus.ESCALATED]),
            Alert.created_at >= cutoff,
        )
        .first()
    )
    if recent_duplicate:
        return None

    message = (
        f"Patient {patient_id}: {assessment.risk_level.value} risk "
        f"(NEWS2={assessment.news2_score}"
        + (f", model probability={assessment.model_probability:.2f}" if assessment.model_probability is not None else "")
        + ")"
    )

    alert = Alert(
        patient_id=patient_id,
        risk_assessment_id=assessment.id,
        severity=severity,
        status=AlertStatus.OPEN,
        message=message,
        dedup_key=key,
        created_at=datetime.utcnow(),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def acknowledge_alert(db: Session, alert_id: int, staff_id: int) -> Optional[Alert]:
    alert = db.get(Alert, alert_id)
    if alert is None:
        return None
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by_id = staff_id
    db.commit()
    db.refresh(alert)
    return alert


def resolve_alert(db: Session, alert_id: int) -> Optional[Alert]:
    alert = db.get(Alert, alert_id)
    if alert is None:
        return None
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(alert)
    return alert


def escalate_stale_alerts(db: Session) -> int:
    """Escalate any OPEN alert that has not been acknowledged within
    ESCALATION_MINUTES. Returns the number of alerts escalated.

    Intended to be run periodically (e.g., by a background scheduler or
    a cron-triggered endpoint) rather than on every request.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=ESCALATION_MINUTES)
    stale = (
        db.query(Alert)
        .filter(Alert.status == AlertStatus.OPEN, Alert.created_at <= cutoff)
        .all()
    )
    for alert in stale:
        alert.status = AlertStatus.ESCALATED
        alert.escalated_at = datetime.utcnow()
    db.commit()
    return len(stale)
