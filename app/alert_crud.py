"""
Alert management — the data access layer for the alert system.

This is where a detected breach (a BreachResult from app/alert_rules.py)
becomes an actual, persisted Alert — including the suppression logic that
prevents alert fatigue, and the state-machine transitions (acknowledge,
escalate, resolve) that make up an alert's lifecycle.

Alert lifecycle:

    open --(acknowledge)--> acknowledged --(resolve)--> resolved
      |                                                     ^
      +--(escalate, if unacknowledged past its time limit)--+--(resolve)
      |
      +--(a duplicate breach arrives while still open)--> stays open,
         occurrence_count increments, a duplicate_suppressed event is
         logged — no new Alert or Notification is created.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app import models
from app.alert_models import (
    Alert, AlertEvent, AlertStatus, AlertEventType, Severity, SEVERITY_RANK,
)
from app.alert_rules import BreachResult, evaluate_vital_threshold, evaluate_activity_patterns
from app.notifications import dispatch_alert
from app.logging_config import access_logger, error_logger


class NotFoundError(Exception):
    pass


class InvalidStateTransitionError(Exception):
    pass


# How long an unacknowledged alert of each severity is allowed to sit
# before it gets escalated. Lower severities are not escalated at all —
# escalation exists specifically so a critical/high alert cannot be
# silently ignored, not to chase every minor notice.
ESCALATION_WINDOWS = {
    Severity.critical: timedelta(minutes=15),
    Severity.high: timedelta(minutes=60),
}

# How long an existing OPEN alert for the same (patient, rule_type)
# suppresses new duplicate breaches, folding them into the existing alert
# instead of creating alert-fatigue spam.
SUPPRESSION_WINDOW = timedelta(minutes=30)


# ---------------------------------------------------------------------
# Creating alerts (with deduplication)
# ---------------------------------------------------------------------

def _find_open_duplicate(db: Session, patient_id: str, rule_type: str) -> Optional[Alert]:
    return db.execute(
        select(Alert).where(
            Alert.patient_id == patient_id,
            Alert.rule_type == rule_type,
            Alert.status.in_([AlertStatus.open, AlertStatus.escalated]),
            Alert.last_occurrence_at >= datetime.now(timezone.utc) - SUPPRESSION_WINDOW,
        ).order_by(Alert.created_at.desc())
    ).scalars().first()


def create_alert_from_breach(db: Session, patient_id: str, breach: BreachResult) -> Alert:
    """Turn one detected breach into a persisted Alert, applying
    suppression: if an open alert for the same patient + rule_type was
    created or last recurred within the suppression window, the new
    breach is folded into it instead of creating a new alert."""
    existing = _find_open_duplicate(db, patient_id, breach.rule_type)

    if existing is not None:
        existing.occurrence_count += 1
        existing.last_occurrence_at = datetime.now(timezone.utc)
        # If this new occurrence is more severe than the alert's current
        # severity, upgrade it — a suppressed duplicate should never hide
        # the fact that things got worse, only that they didn't change.
        upgraded = SEVERITY_RANK[breach.severity] > SEVERITY_RANK[existing.severity]
        if upgraded:
            existing.severity = breach.severity
            existing.message = breach.message
            existing.details = breach.details
        db.add(AlertEvent(
            alert_id=existing.id,
            event_type=AlertEventType.duplicate_suppressed,
            notes=(
                f"Duplicate breach folded into existing alert (occurrence #{existing.occurrence_count})."
                + (" Severity was upgraded." if upgraded else "")
            ),
        ))
        db.commit()
        db.refresh(existing)
        access_logger.info(
            f"Suppressed duplicate breach for patient {patient_id}, rule={breach.rule_type} "
            f"-> alert {existing.id} (occurrence #{existing.occurrence_count}, upgraded={upgraded})"
        )
        if upgraded:
            # A newly-more-severe condition still deserves a fresh notification round.
            dispatch_alert(db, existing)
        return existing

    alert = Alert(
        patient_id=patient_id,
        source_type=breach.source_type,
        rule_type=breach.rule_type,
        severity=breach.severity,
        status=AlertStatus.open,
        message=breach.message,
        details=breach.details,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    db.add(AlertEvent(alert_id=alert.id, event_type=AlertEventType.created, notes="Alert created by rule engine."))
    db.commit()

    access_logger.info(f"Created alert {alert.id} (severity={alert.severity.value}, rule={alert.rule_type}) for patient {patient_id}")
    dispatch_alert(db, alert)
    db.refresh(alert)
    return alert


def process_new_vital_sign(db: Session, vital: models.VitalSigns) -> Optional[Alert]:
    """Entry point called right after a new VitalSigns row is created
    (see app/main.py). Evaluates the threshold rule and, if breached,
    creates/updates an alert."""
    breach = evaluate_vital_threshold(db, vital)
    if breach is None:
        return None
    return create_alert_from_breach(db, str(vital.patient_id), breach)


def process_new_activity_data(db: Session, activity: models.ActivityData) -> list[Alert]:
    """Entry point called right after a new ActivityData row is created.
    Evaluates all active pattern rules for this patient/activity_type and
    creates/updates an alert for each breach found (there can be more
    than one distinct problem at once)."""
    breaches = evaluate_activity_patterns(db, str(activity.patient_id), activity.activity_type)
    return [create_alert_from_breach(db, str(activity.patient_id), b) for b in breaches]


# ---------------------------------------------------------------------
# Reading / listing alerts
# ---------------------------------------------------------------------

def get_alert(db: Session, alert_id: UUID) -> Alert:
    alert = db.get(Alert, str(alert_id))
    if alert is None:
        raise NotFoundError(f"No alert found with id '{alert_id}'.")
    return alert


def list_alerts(
    db: Session, *, patient_id: Optional[UUID] = None, status: Optional[AlertStatus] = None,
    severity: Optional[Severity] = None, start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None, page: int = 1, page_size: int = 50,
) -> tuple[Sequence[Alert], int]:
    page, page_size = max(page, 1), min(max(page_size, 1), 200)

    stmt = select(Alert)
    count_stmt = select(func.count()).select_from(Alert)
    if patient_id is not None:
        stmt = stmt.where(Alert.patient_id == str(patient_id))
        count_stmt = count_stmt.where(Alert.patient_id == str(patient_id))
    if status is not None:
        stmt = stmt.where(Alert.status == status)
        count_stmt = count_stmt.where(Alert.status == status)
    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
        count_stmt = count_stmt.where(Alert.severity == severity)
    if start_date is not None:
        stmt = stmt.where(Alert.created_at >= start_date)
        count_stmt = count_stmt.where(Alert.created_at >= start_date)
    if end_date is not None:
        stmt = stmt.where(Alert.created_at <= end_date)
        count_stmt = count_stmt.where(Alert.created_at <= end_date)

    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(Alert.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    return db.execute(stmt).scalars().all(), total


# ---------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------

def acknowledge_alert(db: Session, alert_id: UUID, user: models.User, notes: Optional[str] = None) -> Alert:
    alert = get_alert(db, alert_id)
    if alert.status not in (AlertStatus.open, AlertStatus.escalated):
        raise InvalidStateTransitionError(
            f"Alert is '{alert.status.value}' and cannot be acknowledged (must be 'open' or 'escalated')."
        )
    alert.status = AlertStatus.acknowledged
    alert.acknowledged_by = user.id
    alert.acknowledged_at = datetime.now(timezone.utc)
    db.add(AlertEvent(alert_id=alert.id, event_type=AlertEventType.acknowledged, performed_by=user.id, notes=notes))
    db.commit()
    db.refresh(alert)
    access_logger.info(f"Alert {alert.id} acknowledged by {user.email}")
    return alert


def resolve_alert(db: Session, alert_id: UUID, user: models.User, notes: str) -> Alert:
    alert = get_alert(db, alert_id)
    if alert.status == AlertStatus.resolved:
        raise InvalidStateTransitionError("Alert is already resolved.")
    alert.status = AlertStatus.resolved
    alert.resolved_by = user.id
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolution_notes = notes
    db.add(AlertEvent(alert_id=alert.id, event_type=AlertEventType.resolved, performed_by=user.id, notes=notes))
    db.commit()
    db.refresh(alert)
    access_logger.info(f"Alert {alert.id} resolved by {user.email}")
    return alert


def add_note(db: Session, alert_id: UUID, user: models.User, notes: str) -> Alert:
    alert = get_alert(db, alert_id)
    db.add(AlertEvent(alert_id=alert.id, event_type=AlertEventType.note_added, performed_by=user.id, notes=notes))
    db.commit()
    db.refresh(alert)
    return alert


def run_escalation_check(db: Session, now: Optional[datetime] = None) -> list[Alert]:
    """Find every open alert whose severity has an escalation window, and
    whose window has elapsed without being acknowledged, and escalate it.

    In production this is intended to run on a schedule (e.g. a cron job
    or Celery beat task every minute — see the System Integration Guide),
    but is exposed here as a plain function (and a POST endpoint) so it
    can also be triggered on demand, including from tests.
    """
    now = now or datetime.now(timezone.utc)
    escalated = []

    for severity, window in ESCALATION_WINDOWS.items():
        cutoff = now - window
        candidates = db.execute(
            select(Alert).where(
                Alert.status == AlertStatus.open,
                Alert.severity == severity,
                Alert.created_at <= cutoff,
            )
        ).scalars().all()

        for alert in candidates:
            alert.status = AlertStatus.escalated
            alert.escalated_at = now
            db.add(AlertEvent(
                alert_id=alert.id, event_type=AlertEventType.escalated,
                notes=f"Unacknowledged for over {window}; escalated automatically.",
            ))
            db.commit()
            db.refresh(alert)
            dispatch_alert(db, alert, escalation=True)
            db.refresh(alert)
            escalated.append(alert)
            access_logger.warning(f"Escalated alert {alert.id} (severity={severity.value}, unacknowledged past {window})")

    return escalated
