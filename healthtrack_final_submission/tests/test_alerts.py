import os
import sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import alerts
from app.models import Patient, RiskAssessment, RiskLevel, Alert, AlertStatus


def _make_patient(db):
    p = Patient(
        medical_record_number="MRN-TEST-1", first_name="Test", last_name="Patient",
        date_of_birth=datetime(1990, 1, 1)
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_assessment(db, patient, risk_level, news2=9):
    a = RiskAssessment(
        patient_id=patient.id, news2_score=news2,
        model_probability=0.8, risk_level=risk_level
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_low_risk_creates_no_alert(db_session):
    patient = _make_patient(db_session)
    assessment = _make_assessment(db_session, patient, RiskLevel.LOW, news2=0)
    alert = alerts.create_alert_from_assessment(db_session, patient.id, assessment)
    assert alert is None


def test_critical_risk_creates_alert(db_session):
    patient = _make_patient(db_session)
    assessment = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    alert = alerts.create_alert_from_assessment(db_session, patient.id, assessment)
    assert alert is not None
    assert alert.severity.value == "Critical"
    assert alert.status == AlertStatus.OPEN


def test_duplicate_alert_within_cooldown_is_suppressed(db_session):
    patient = _make_patient(db_session)
    a1 = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    first = alerts.create_alert_from_assessment(db_session, patient.id, a1)
    assert first is not None

    a2 = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    second = alerts.create_alert_from_assessment(db_session, patient.id, a2)
    assert second is None  # deduped, same patient+risk level within cooldown


def test_alert_allowed_after_cooldown_expires(db_session):
    patient = _make_patient(db_session)
    a1 = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    first = alerts.create_alert_from_assessment(db_session, patient.id, a1)
    assert first is not None

    # simulate the first alert having been created outside the cooldown window
    first.created_at = datetime.utcnow() - timedelta(minutes=alerts.DEDUP_COOLDOWN_MINUTES + 5)
    db_session.commit()

    a2 = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    second = alerts.create_alert_from_assessment(db_session, patient.id, a2)
    assert second is not None


def test_acknowledge_alert_changes_status(db_session):
    patient = _make_patient(db_session)
    assessment = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    alert = alerts.create_alert_from_assessment(db_session, patient.id, assessment)

    updated = alerts.acknowledge_alert(db_session, alert.id, staff_id=1)
    assert updated.status.value == "Acknowledged"
    assert updated.acknowledged_by_id == 1


def test_resolve_alert_sets_resolved_at(db_session):
    patient = _make_patient(db_session)
    assessment = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    alert = alerts.create_alert_from_assessment(db_session, patient.id, assessment)

    resolved = alerts.resolve_alert(db_session, alert.id)
    assert resolved.status.value == "Resolved"
    assert resolved.resolved_at is not None


def test_escalate_stale_alerts_only_escalates_old_open_ones(db_session):
    patient = _make_patient(db_session)
    assessment = _make_assessment(db_session, patient, RiskLevel.CRITICAL)
    alert = alerts.create_alert_from_assessment(db_session, patient.id, assessment)

    # fresh alert should NOT be escalated yet
    count = alerts.escalate_stale_alerts(db_session)
    assert count == 0

    # backdate it past the escalation threshold
    alert.created_at = datetime.utcnow() - timedelta(minutes=alerts.ESCALATION_MINUTES + 5)
    db_session.commit()

    count = alerts.escalate_stale_alerts(db_session)
    assert count == 1
    db_session.refresh(alert)
    assert alert.status == AlertStatus.ESCALATED
    assert alert.escalated_at is not None
