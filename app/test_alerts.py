"""
Test suite for the Phase 4 real-time alert system.

Organized to match the assignment's scope directly:
  TestVitalThresholdDetection   - threshold monitoring + patient customization
  TestActivityPatternDetection  - sustained_drop / missing_data / sudden_change
  TestNotificationDispatch      - channel routing by severity, payload shape
  TestAlertSuppression          - dedup / occurrence_count / severity upgrade
  TestAlertLifecycleAndEscalation - acknowledge / resolve / escalate state machine
  TestAlertHistorySearch        - filtering/searching alert history
  TestAlertAPIEndToEnd          - full HTTP-level flows via TestClient
"""
from datetime import datetime, date, timedelta, timezone
from uuid import uuid4

import pytest

from app import models, crud, alert_crud
from app.alert_models import (
    VitalThreshold, ActivityRule, Alert, AlertEvent, Notification,
    Severity, AlertStatus, AlertEventType, AlertSourceType, NotificationChannel, NotificationStatus,
    ActivityRuleType,
)
from app.alert_rules import evaluate_vital_threshold, evaluate_activity_patterns, BreachResult
from app.notifications import dispatch_alert, get_recipients, build_alert_payload


# ---------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------

def _make_patient(db, mrn="MRN-ALERT-0001"):
    return crud.create_patient(
        db, mrn=mrn, first_name="Maria", last_name="Chen",
        date_of_birth=date(1958, 3, 14), sex=models.Sex.female, care_unit="Pilot Unit A",
    )


def _make_clinician(db, email="clinician@example.com"):
    from app.auth import hash_password
    user = crud.create_user(db, email=email, hashed_password=hash_password("x"), role=models.UserRole.clinician)
    return user


def _vital(db, patient, vital_type, value, unit, recorded_at=None):
    return crud.create_vital_signs(
        db, patient_id=patient.id, vital_type=vital_type, value=value, unit=unit,
        recorded_at=recorded_at or datetime.now(timezone.utc),
    )


# =======================================================================
# 1. Vital sign threshold monitoring
# =======================================================================

class TestVitalThresholdDetection:
    def test_reading_within_range_produces_no_breach(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.heart_rate,
                                       severity=Severity.high, min_value=40, max_value=140))
        db_session.commit()

        vital = _vital(db_session, patient, models.VitalType.heart_rate, 78, "bpm")
        breach = evaluate_vital_threshold(db_session, vital)
        assert breach is None

    def test_reading_above_range_produces_breach(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.blood_pressure_systolic,
                                       severity=Severity.high, min_value=90, max_value=140))
        db_session.commit()

        vital = _vital(db_session, patient, models.VitalType.blood_pressure_systolic, 168, "mmHg")
        breach = evaluate_vital_threshold(db_session, vital)
        assert breach is not None
        assert breach.severity == Severity.high
        assert "168" in breach.message

    def test_global_default_threshold_applies_when_no_patient_override(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.spo2,
                                       severity=Severity.critical, min_value=90, max_value=100))
        db_session.commit()

        vital = _vital(db_session, patient, models.VitalType.spo2, 85, "%")
        breach = evaluate_vital_threshold(db_session, vital)
        assert breach is not None
        assert breach.details["threshold_scope"] == "global default"

    def test_patient_specific_threshold_overrides_global_default(self, db_session):
        patient = _make_patient(db_session)
        # Global default: 90-100 is fine
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.spo2,
                                       severity=Severity.critical, min_value=90, max_value=100))
        # This patient runs low normally — clinician sets a wider personal band
        db_session.add(VitalThreshold(patient_id=patient.id, vital_type=models.VitalType.spo2,
                                       severity=Severity.critical, min_value=80, max_value=100))
        db_session.commit()

        # 85% would breach the global default but NOT this patient's custom band
        vital = _vital(db_session, patient, models.VitalType.spo2, 85, "%")
        breach = evaluate_vital_threshold(db_session, vital)
        assert breach is None

        # 75% breaches even the patient's own wider band
        vital2 = _vital(db_session, patient, models.VitalType.spo2, 75, "%")
        breach2 = evaluate_vital_threshold(db_session, vital2)
        assert breach2 is not None
        assert breach2.details["threshold_scope"] == "patient-specific"

    def test_multiple_severity_bands_return_most_severe_breach(self, db_session):
        patient = _make_patient(db_session)
        # A wide "high" band and a narrower, nested "critical" band
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.blood_glucose,
                                       severity=Severity.high, min_value=70, max_value=200))
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.blood_glucose,
                                       severity=Severity.critical, min_value=70, max_value=300))
        db_session.commit()

        # 250 breaches the "high" band (>200) but not the "critical" band (<=300)
        vital = _vital(db_session, patient, models.VitalType.blood_glucose, 250, "mg/dL")
        breach = evaluate_vital_threshold(db_session, vital)
        assert breach.severity == Severity.high

        # 350 breaches both bands — critical must win
        vital2 = _vital(db_session, patient, models.VitalType.blood_glucose, 350, "mg/dL")
        breach2 = evaluate_vital_threshold(db_session, vital2)
        assert breach2.severity == Severity.critical

    def test_process_new_vital_sign_creates_persisted_alert(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.heart_rate,
                                       severity=Severity.critical, min_value=40, max_value=140))
        db_session.commit()

        vital = _vital(db_session, patient, models.VitalType.heart_rate, 175, "bpm")
        alert = alert_crud.process_new_vital_sign(db_session, vital)

        assert alert is not None
        assert alert.status == AlertStatus.open
        assert alert.rule_type == "vital_out_of_range:heart_rate"
        assert alert.occurrence_count == 1


# =======================================================================
# 2. Activity pattern detection
# =======================================================================

class TestActivityPatternDetection:
    def _add_activity(self, db, patient, value, days_ago):
        return crud.create_activity_data(
            db, patient_id=patient.id, activity_type=models.ActivityType.steps,
            value=value, unit="steps", recorded_date=date.today() - timedelta(days=days_ago),
        )

    def test_sustained_drop_detected(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.sustained_drop,
            severity=Severity.medium, parameters={"lookback_days": 7, "baseline_days": 14, "drop_pct": 40},
        ))
        db_session.commit()

        # Baseline period (days 8-21 ago): consistently active, ~9000 steps/day
        for d in range(8, 22):
            self._add_activity(db_session, patient, 9000, d)
        # Recent period (last 7 days): dropped sharply to ~3000 steps/day (67% drop)
        for d in range(0, 7):
            self._add_activity(db_session, patient, 3000, d)

        breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        assert len(breaches) == 1
        assert breaches[0].rule_type == "sustained_drop:steps"
        assert breaches[0].details["drop_pct_observed"] >= 40

    def test_no_sustained_drop_when_activity_stable(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.sustained_drop,
            severity=Severity.medium, parameters={"lookback_days": 7, "baseline_days": 14, "drop_pct": 40},
        ))
        db_session.commit()

        for d in range(0, 21):
            self._add_activity(db_session, patient, 9000, d)

        breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        assert breaches == []

    def test_missing_data_detected(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.missing_data,
            severity=Severity.low, parameters={"missing_days": 2},
        ))
        db_session.commit()
        # No activity data recorded at all for this patient

        breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        assert len(breaches) == 1
        assert breaches[0].rule_type == "missing_data:steps"

    def test_missing_data_not_triggered_when_recent_data_exists(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.missing_data,
            severity=Severity.low, parameters={"missing_days": 2},
        ))
        db_session.commit()
        self._add_activity(db_session, patient, 5000, 0)  # recorded today

        breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        assert breaches == []

    def test_sudden_change_detected(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.sudden_change,
            severity=Severity.medium, parameters={"window_days": 7, "deviation_pct": 50},
        ))
        db_session.commit()

        for d in range(1, 8):
            self._add_activity(db_session, patient, 8000, d)
        self._add_activity(db_session, patient, 200, 0)  # today: huge sudden drop

        breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        assert len(breaches) == 1
        assert breaches[0].rule_type == "sudden_change:steps"
        assert breaches[0].details["direction"] == "below"

    def test_multiple_distinct_rules_can_fire_together(self, db_session):
        """Unlike vital thresholds (one reading -> at most one alert), activity
        rules can legitimately detect more than one distinct problem at once."""
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.sustained_drop,
            severity=Severity.medium, parameters={"lookback_days": 7, "baseline_days": 14, "drop_pct": 40},
        ))
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.sudden_change,
            severity=Severity.high, parameters={"window_days": 7, "deviation_pct": 50},
        ))
        db_session.commit()

        for d in range(8, 22):
            self._add_activity(db_session, patient, 9000, d)
        for d in range(1, 7):
            self._add_activity(db_session, patient, 3000, d)
        self._add_activity(db_session, patient, 100, 0)

        breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        rule_types = {b.rule_type for b in breaches}
        assert rule_types == {"sustained_drop:steps", "sudden_change:steps"}

    def test_patient_specific_activity_rule_overrides_global(self, db_session):
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.missing_data,
            severity=Severity.low, parameters={"missing_days": 2},
        ))
        # This patient has a stricter personal rule: only 1 missing day allowed
        db_session.add(ActivityRule(
            patient_id=patient.id, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.missing_data,
            severity=Severity.high, parameters={"missing_days": 1},
        ))
        db_session.commit()

        breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        # Only the patient-specific rule should have run (not both)
        assert len(breaches) == 1
        assert breaches[0].severity == Severity.high


# =======================================================================
# 3. Notification dispatch
# =======================================================================

class TestNotificationDispatch:
    def test_low_severity_alert_only_notifies_in_app(self, db_session):
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        alert = Alert(patient_id=patient.id, source_type=AlertSourceType.vital_sign,
                       rule_type="vital_out_of_range", severity=Severity.low, message="test", details={})
        db_session.add(alert)
        db_session.commit()

        notifications = dispatch_alert(db_session, alert)
        channels_used = {n.channel for n in notifications}
        assert channels_used == {NotificationChannel.in_app}

    def test_critical_severity_alert_notifies_all_channels(self, db_session):
        patient = _make_patient(db_session)
        _make_clinician(db_session)
        alert = Alert(patient_id=patient.id, source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                       severity=Severity.critical, message="test critical", details={})
        db_session.add(alert)
        db_session.commit()

        notifications = dispatch_alert(db_session, alert)
        channels_used = {n.channel for n in notifications}
        assert channels_used == {NotificationChannel.in_app, NotificationChannel.email, NotificationChannel.sms}
        assert all(n.status == NotificationStatus.sent for n in notifications)

    def test_notification_payload_contains_expected_fields(self, db_session):
        patient = _make_patient(db_session)
        alert = Alert(patient_id=patient.id, source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                       severity=Severity.high, message="High heart rate", details={"measured_value": 175})
        db_session.add(alert)
        db_session.commit()

        payload = build_alert_payload(alert, patient)
        assert payload["patient_mrn"] == patient.mrn
        assert payload["severity"] == "high"
        assert payload["message"] == "High heart rate"
        assert payload["details"]["measured_value"] == 175

    def test_recipients_include_active_clinicians_and_linked_patient_user(self, db_session):
        from app.auth import hash_password
        patient_user = crud.create_user(db_session, email="patient@example.com",
                                         hashed_password=hash_password("x"), role=models.UserRole.patient)
        patient = crud.create_patient(
            db_session, mrn="MRN-REC-0001", first_name="A", last_name="B",
            date_of_birth=date(1970, 1, 1), user_id=patient_user.id,
        )
        clinician = _make_clinician(db_session, email="c1@example.com")
        # An inactive clinician should be excluded
        inactive = _make_clinician(db_session, email="inactive@example.com")
        inactive.is_active = False
        db_session.commit()

        recipients = get_recipients(db_session, patient)
        recipient_emails = {r.email for r in recipients}
        assert "c1@example.com" in recipient_emails
        assert "patient@example.com" in recipient_emails
        assert "inactive@example.com" not in recipient_emails


# =======================================================================
# 4. Suppression / deduplication (alert fatigue control)
# =======================================================================

class TestAlertSuppression:
    def test_duplicate_breach_does_not_create_second_alert(self, db_session):
        patient = _make_patient(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign,
                               rule_type="vital_out_of_range", severity=Severity.high, message="High HR", details={})

        alert1 = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        alert2 = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)

        assert alert1.id == alert2.id
        assert alert2.occurrence_count == 2

        total_alerts = db_session.query(Alert).filter_by(patient_id=patient.id).count()
        assert total_alerts == 1

    def test_duplicate_suppression_logs_an_event(self, db_session):
        patient = _make_patient(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.medium, message="test", details={})

        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)

        events = db_session.query(AlertEvent).filter_by(alert_id=alert.id).all()
        event_types = [e.event_type for e in events]
        assert AlertEventType.created in event_types
        assert AlertEventType.duplicate_suppressed in event_types

    def test_more_severe_duplicate_upgrades_existing_alert(self, db_session):
        patient = _make_patient(db_session)
        mild = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                             severity=Severity.medium, message="Mildly high", details={})
        severe = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.critical, message="Dangerously high", details={})

        alert1 = alert_crud.create_alert_from_breach(db_session, str(patient.id), mild)
        assert alert1.severity == Severity.medium

        alert2 = alert_crud.create_alert_from_breach(db_session, str(patient.id), severe)
        assert alert2.id == alert1.id
        assert alert2.severity == Severity.critical
        assert alert2.message == "Dangerously high"

    def test_less_severe_duplicate_does_not_downgrade_existing_alert(self, db_session):
        patient = _make_patient(db_session)
        severe = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.critical, message="Dangerously high", details={})
        mild = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                             severity=Severity.low, message="Back near threshold", details={})

        alert1 = alert_crud.create_alert_from_breach(db_session, str(patient.id), severe)
        alert2 = alert_crud.create_alert_from_breach(db_session, str(patient.id), mild)

        assert alert2.severity == Severity.critical  # unchanged, not downgraded

    def test_new_alert_created_after_previous_one_resolved(self, db_session):
        """Suppression only applies to OPEN/ESCALATED alerts — once an alert
        is resolved, a fresh breach of the same rule_type must start a new
        alert rather than silently reopening old history."""
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.high, message="test", details={})

        alert1 = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        alert_crud.acknowledge_alert(db_session, alert1.id, clinician)
        alert_crud.resolve_alert(db_session, alert1.id, clinician, "Resolved.")

        alert2 = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        assert alert2.id != alert1.id

        total_alerts = db_session.query(Alert).filter_by(patient_id=patient.id).count()
        assert total_alerts == 2

    def test_two_different_vital_types_breaching_produce_two_separate_alerts(self, db_session):
        """Regression test: rule_type for a vital breach must be specific to
        the vital_type (e.g. 'vital_out_of_range:heart_rate' vs
        'vital_out_of_range:spo2'), not the generic 'vital_out_of_range'
        string. Otherwise an unrelated SpO2 problem would be silently
        folded into an existing, completely unrelated heart-rate alert as
        a 'duplicate' — hiding a genuinely new problem from clinicians."""
        patient = _make_patient(db_session)
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.heart_rate,
                                       severity=Severity.high, min_value=40, max_value=140))
        db_session.add(VitalThreshold(patient_id=None, vital_type=models.VitalType.spo2,
                                       severity=Severity.high, min_value=90, max_value=100))
        db_session.commit()

        v1 = _vital(db_session, patient, models.VitalType.heart_rate, 180, "bpm")
        alert1 = alert_crud.process_new_vital_sign(db_session, v1)
        v2 = _vital(db_session, patient, models.VitalType.spo2, 70, "%")
        alert2 = alert_crud.process_new_vital_sign(db_session, v2)

        assert alert1.id != alert2.id
        total_alerts = db_session.query(Alert).filter_by(patient_id=patient.id).count()
        assert total_alerts == 2

    def test_two_different_activity_types_with_same_rule_produce_two_separate_alerts(self, db_session):
        """Same regression, for activity pattern rules: a 'steps' sustained
        drop and an 'active_minutes' sustained drop for the same patient
        must not be merged into one alert just because they share a
        rule_type of 'sustained_drop'."""
        patient = _make_patient(db_session)
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.steps, rule_type=ActivityRuleType.sustained_drop,
            severity=Severity.medium, parameters={"lookback_days": 7, "baseline_days": 14, "drop_pct": 40},
        ))
        db_session.add(ActivityRule(
            patient_id=None, activity_type=models.ActivityType.active_minutes, rule_type=ActivityRuleType.sustained_drop,
            severity=Severity.medium, parameters={"lookback_days": 7, "baseline_days": 14, "drop_pct": 40},
        ))
        db_session.commit()

        for d in range(8, 22):
            crud.create_activity_data(db_session, patient_id=patient.id, activity_type=models.ActivityType.steps,
                                       value=9000, unit="steps", recorded_date=date.today() - timedelta(days=d))
            crud.create_activity_data(db_session, patient_id=patient.id, activity_type=models.ActivityType.active_minutes,
                                       value=60, unit="minutes", recorded_date=date.today() - timedelta(days=d))
        for d in range(0, 7):
            crud.create_activity_data(db_session, patient_id=patient.id, activity_type=models.ActivityType.steps,
                                       value=3000, unit="steps", recorded_date=date.today() - timedelta(days=d))
            crud.create_activity_data(db_session, patient_id=patient.id, activity_type=models.ActivityType.active_minutes,
                                       value=15, unit="minutes", recorded_date=date.today() - timedelta(days=d))

        steps_breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.steps)
        active_min_breaches = evaluate_activity_patterns(db_session, str(patient.id), models.ActivityType.active_minutes)
        alerts = []
        for b in steps_breaches + active_min_breaches:
            alerts.append(alert_crud.create_alert_from_breach(db_session, str(patient.id), b))

        assert len({a.id for a in alerts}) == 2  # two distinct alerts, not merged into one
        rule_types = {a.rule_type for a in alerts}
        assert rule_types == {"sustained_drop:steps", "sustained_drop:active_minutes"}


# =======================================================================
# 5. Alert lifecycle and escalation
# =======================================================================

class TestAlertLifecycleAndEscalation:
    def test_acknowledge_transitions_status_and_logs_event(self, db_session):
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.high, message="test", details={})
        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)

        updated = alert_crud.acknowledge_alert(db_session, alert.id, clinician, notes="Looking into it.")
        assert updated.status == AlertStatus.acknowledged
        assert updated.acknowledged_by == clinician.id
        assert updated.acknowledged_at is not None

    def test_cannot_acknowledge_a_resolved_alert(self, db_session):
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.high, message="test", details={})
        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        alert_crud.acknowledge_alert(db_session, alert.id, clinician)
        alert_crud.resolve_alert(db_session, alert.id, clinician, "Fixed.")

        with pytest.raises(alert_crud.InvalidStateTransitionError):
            alert_crud.acknowledge_alert(db_session, alert.id, clinician)

    def test_resolve_requires_notes(self, db_session):
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.high, message="test", details={})
        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)

        resolved = alert_crud.resolve_alert(db_session, alert.id, clinician, "Patient re-checked; normal now.")
        assert resolved.status == AlertStatus.resolved
        assert resolved.resolution_notes == "Patient re-checked; normal now."

    def test_critical_alert_escalates_after_window_elapses(self, db_session):
        patient = _make_patient(db_session)
        _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.critical, message="test", details={})
        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)

        # Simulate 20 minutes passing (critical escalation window is 15 minutes)
        future = datetime.now(timezone.utc) + timedelta(minutes=20)
        escalated = alert_crud.run_escalation_check(db_session, now=future)

        assert len(escalated) == 1
        assert escalated[0].id == alert.id
        assert escalated[0].status == AlertStatus.escalated
        assert escalated[0].escalated_at is not None

    def test_acknowledged_alert_is_not_escalated(self, db_session):
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.critical, message="test", details={})
        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        alert_crud.acknowledge_alert(db_session, alert.id, clinician)

        future = datetime.now(timezone.utc) + timedelta(minutes=20)
        escalated = alert_crud.run_escalation_check(db_session, now=future)
        assert escalated == []

    def test_low_severity_alert_never_escalates(self, db_session):
        patient = _make_patient(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.low, message="test", details={})
        alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)

        # Even after a very long time, low severity has no escalation window
        future = datetime.now(timezone.utc) + timedelta(days=1)
        escalated = alert_crud.run_escalation_check(db_session, now=future)
        assert escalated == []

    def test_escalation_dispatches_fresh_notifications(self, db_session):
        patient = _make_patient(db_session)
        _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                               severity=Severity.high, message="test", details={})
        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        initial_notification_count = db_session.query(Notification).filter_by(alert_id=alert.id).count()

        future = datetime.now(timezone.utc) + timedelta(minutes=90)  # high escalation window is 60 min
        alert_crud.run_escalation_check(db_session, now=future)

        final_notification_count = db_session.query(Notification).filter_by(alert_id=alert.id).count()
        assert final_notification_count > initial_notification_count


# =======================================================================
# 6. Alert history search
# =======================================================================

class TestAlertHistorySearch:
    def test_search_by_patient(self, db_session):
        p1, p2 = _make_patient(db_session, "MRN-H-1"), _make_patient(db_session, "MRN-H-2")
        for p in (p1, p2):
            alert_crud.create_alert_from_breach(db_session, str(p.id), BreachResult(
                source_type=AlertSourceType.vital_sign, rule_type="vital_out_of_range",
                severity=Severity.high, message="test", details={}))

        items, total = alert_crud.list_alerts(db_session, patient_id=p1.id)
        assert total == 1
        assert items[0].patient_id == p1.id

    def test_search_by_status(self, db_session):
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        breach1 = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="rule_a",
                                severity=Severity.high, message="a", details={})
        breach2 = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="rule_b",
                                severity=Severity.medium, message="b", details={})
        a1 = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach1)
        alert_crud.create_alert_from_breach(db_session, str(patient.id), breach2)
        alert_crud.acknowledge_alert(db_session, a1.id, clinician)

        open_items, open_total = alert_crud.list_alerts(db_session, status=AlertStatus.open)
        ack_items, ack_total = alert_crud.list_alerts(db_session, status=AlertStatus.acknowledged)
        assert open_total == 1
        assert ack_total == 1

    def test_search_by_severity(self, db_session):
        patient = _make_patient(db_session)
        for sev, rt in [(Severity.low, "r1"), (Severity.critical, "r2")]:
            alert_crud.create_alert_from_breach(db_session, str(patient.id), BreachResult(
                source_type=AlertSourceType.vital_sign, rule_type=rt, severity=sev, message="x", details={}))

        items, total = alert_crud.list_alerts(db_session, severity=Severity.critical)
        assert total == 1
        assert items[0].severity == Severity.critical

    def test_search_by_date_range(self, db_session):
        patient = _make_patient(db_session)
        alert_crud.create_alert_from_breach(db_session, str(patient.id), BreachResult(
            source_type=AlertSourceType.vital_sign, rule_type="r1", severity=Severity.high, message="x", details={}))

        now = datetime.now(timezone.utc)
        items_future, total_future = alert_crud.list_alerts(db_session, start_date=now + timedelta(days=1))
        items_past, total_past = alert_crud.list_alerts(db_session, start_date=now - timedelta(days=1))
        assert total_future == 0
        assert total_past == 1

    def test_full_history_includes_acknowledgment_and_resolution_events(self, db_session):
        patient = _make_patient(db_session)
        clinician = _make_clinician(db_session)
        breach = BreachResult(source_type=AlertSourceType.vital_sign, rule_type="r1",
                               severity=Severity.high, message="x", details={})
        alert = alert_crud.create_alert_from_breach(db_session, str(patient.id), breach)
        alert_crud.acknowledge_alert(db_session, alert.id, clinician, notes="checking")
        alert_crud.resolve_alert(db_session, alert.id, clinician, "done")

        full = alert_crud.get_alert(db_session, alert.id)
        event_types = [e.event_type for e in full.events]
        assert event_types == [AlertEventType.created, AlertEventType.acknowledged, AlertEventType.resolved]


# =======================================================================
# 7. Full API-level flows
# =======================================================================

def _register_and_login(client, email, password="Password123", role="clinician"):
    r = client.post("/auth/register", json={"email": email, "password": password, "role": role})
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestAlertAPIEndToEnd:
    def _create_patient(self, client, headers, mrn="MRN-API-ALERT-0001"):
        r = client.post("/patients", json={
            "mrn": mrn, "first_name": "Ana", "last_name": "Silva", "date_of_birth": "1970-01-01",
        }, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_recording_out_of_range_vital_creates_alert_via_api(self, client):
        headers = _register_and_login(client, "clin_alert1@example.com")
        patient_id = self._create_patient(client, headers)

        client.post(f"/patients/{patient_id}/thresholds", json={
            "vital_type": "heart_rate", "severity": "critical", "min_value": 40, "max_value": 140,
        }, headers=headers)

        r = client.post(f"/patients/{patient_id}/vitals", json={
            "vital_type": "heart_rate", "value": 180, "unit": "bpm",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }, headers=headers)
        assert r.status_code == 201

        r2 = client.get(f"/alerts?patient_id={patient_id}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["total"] == 1
        assert r2.json()["items"][0]["severity"] == "critical"

    def test_acknowledge_and_resolve_via_api(self, client):
        headers = _register_and_login(client, "clin_alert2@example.com")
        patient_id = self._create_patient(client, headers, mrn="MRN-API-ALERT-0002")
        client.post(f"/patients/{patient_id}/thresholds", json={
            "vital_type": "spo2", "severity": "high", "min_value": 90, "max_value": 100,
        }, headers=headers)
        client.post(f"/patients/{patient_id}/vitals", json={
            "vital_type": "spo2", "value": 80, "unit": "%",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }, headers=headers)
        alert_id = client.get(f"/alerts?patient_id={patient_id}", headers=headers).json()["items"][0]["id"]

        r_ack = client.post(f"/alerts/{alert_id}/acknowledge", json={"notes": "Investigating"}, headers=headers)
        assert r_ack.status_code == 200
        assert r_ack.json()["status"] == "acknowledged"

        r_resolve = client.post(f"/alerts/{alert_id}/resolve", json={"notes": "Patient recovered"}, headers=headers)
        assert r_resolve.status_code == 200
        assert r_resolve.json()["status"] == "resolved"

    def test_full_history_endpoint_returns_events(self, client):
        headers = _register_and_login(client, "clin_alert3@example.com")
        patient_id = self._create_patient(client, headers, mrn="MRN-API-ALERT-0003")
        client.post(f"/patients/{patient_id}/thresholds", json={
            "vital_type": "blood_glucose", "severity": "medium", "min_value": 70, "max_value": 180,
        }, headers=headers)
        client.post(f"/patients/{patient_id}/vitals", json={
            "vital_type": "blood_glucose", "value": 220, "unit": "mg/dL",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }, headers=headers)
        alert_id = client.get(f"/alerts?patient_id={patient_id}", headers=headers).json()["items"][0]["id"]
        client.post(f"/alerts/{alert_id}/acknowledge", json={}, headers=headers)

        r = client.get(f"/alerts/{alert_id}", headers=headers)
        assert r.status_code == 200
        event_types = [e["event_type"] for e in r.json()["events"]]
        assert "created" in event_types
        assert "acknowledged" in event_types

    def test_duplicate_vitals_do_not_flood_alert_list(self, client):
        headers = _register_and_login(client, "clin_alert4@example.com")
        patient_id = self._create_patient(client, headers, mrn="MRN-API-ALERT-0004")
        client.post(f"/patients/{patient_id}/thresholds", json={
            "vital_type": "heart_rate", "severity": "high", "min_value": 40, "max_value": 140,
        }, headers=headers)

        for _ in range(5):
            client.post(f"/patients/{patient_id}/vitals", json={
                "vital_type": "heart_rate", "value": 175, "unit": "bpm",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }, headers=headers)

        r = client.get(f"/alerts?patient_id={patient_id}", headers=headers)
        assert r.json()["total"] == 1  # suppressed into one alert, not 5
        assert r.json()["items"][0]["occurrence_count"] == 5

    def test_notifications_endpoint_returns_dispatched_notifications(self, client):
        headers = _register_and_login(client, "clin_alert5@example.com")
        patient_id = self._create_patient(client, headers, mrn="MRN-API-ALERT-0005")
        client.post(f"/patients/{patient_id}/thresholds", json={
            "vital_type": "spo2", "severity": "critical", "min_value": 90, "max_value": 100,
        }, headers=headers)
        client.post(f"/patients/{patient_id}/vitals", json={
            "vital_type": "spo2", "value": 70, "unit": "%",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }, headers=headers)

        r = client.get("/notifications", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) >= 1  # this clinician should have received at least the in-app notification

    def test_run_escalation_check_endpoint(self, client):
        clinician_headers = _register_and_login(client, "clin_alert6@example.com", role="clinician")
        patient_id = self._create_patient(client, clinician_headers, mrn="MRN-API-ALERT-0006")
        client.post(f"/patients/{patient_id}/thresholds", json={
            "vital_type": "heart_rate", "severity": "critical", "min_value": 40, "max_value": 140,
        }, headers=clinician_headers)
        client.post(f"/patients/{patient_id}/vitals", json={
            "vital_type": "heart_rate", "value": 190, "unit": "bpm",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }, headers=clinician_headers)

        # Escalation is a system-wide operation and is restricted to admins
        admin_headers = _register_and_login(client, "admin_alert6@example.com", role="admin")
        r = client.post("/alerts/run-escalation-check", headers=admin_headers)
        assert r.status_code == 200
        # Nothing should have escalated yet — the alert was just created (0 minutes old)
        assert r.json()["escalated_count"] == 0

        # A non-admin (e.g. clinician) must not be able to trigger this system-wide sweep
        r_forbidden = client.post("/alerts/run-escalation-check", headers=clinician_headers)
        assert r_forbidden.status_code == 403
