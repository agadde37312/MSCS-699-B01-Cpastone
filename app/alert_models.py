"""
Phase 4 — Real-Time Alert System: data models.

These models extend the Phase 3 data layer (Patient, VitalSigns,
ActivityData, User) without modifying it. Every table here references
patients.id / users.id by foreign key only, so this module can be dropped
into the existing Phase 3 codebase without touching app/models.py.

Design summary (see docs/Alert_Configuration_Document.docx for the full
plain-English explanation):

- VitalThreshold: defines what "normal" looks like for one vital sign,
  either globally (patient_id is NULL, applies to everyone by default) or
  for one specific patient (patient_id set, overrides the global default).
  A single vital_type can have multiple threshold rows at different
  severities (e.g. a wider "high" band and a narrower "critical" band),
  so a large deviation triggers a more urgent alert than a small one.

- ActivityRule: same idea, but for activity-pattern detection (sustained
  drops, missing data, sudden change) rather than a single-reading range.

- Alert: one row per detected problem. Duplicate breaches of the same
  condition while an alert is still open do not create new rows (see
  app/alert_rules.py suppression logic) — they update occurrence_count on
  the existing alert instead, which is how alert fatigue is controlled.

- AlertEvent: an append-only audit trail per alert (created, acknowledged,
  escalated, resolved, suppressed_duplicate, note_added). Never updated or
  deleted, only inserted, so the full history of an alert can always be
  reconstructed exactly as it happened.

- Notification: one row per (alert, recipient, channel) delivery attempt.
"""
import enum
import uuid as _uuid_mod

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, Integer,
    CheckConstraint, Index, Enum as SAEnum, Text, JSON
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.database import Base
from app.models import GUID, VitalType, ActivityType


def _uuid_default():
    return str(_uuid_mod.uuid4())


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------

class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# Ordered list used to compare severities ("is this breach worse than that one?")
SEVERITY_RANK = {Severity.low: 0, Severity.medium: 1, Severity.high: 2, Severity.critical: 3}


class AlertStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"
    escalated = "escalated"
    resolved = "resolved"
    suppressed = "suppressed"  # terminal state for a duplicate that was folded into an existing alert


class AlertSourceType(str, enum.Enum):
    vital_sign = "vital_sign"
    activity = "activity"


class AlertEventType(str, enum.Enum):
    created = "created"
    duplicate_suppressed = "duplicate_suppressed"
    acknowledged = "acknowledged"
    escalated = "escalated"
    resolved = "resolved"
    note_added = "note_added"


class NotificationChannel(str, enum.Enum):
    in_app = "in_app"
    email = "email"
    sms = "sms"


class NotificationStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"


class ActivityRuleType(str, enum.Enum):
    sustained_drop = "sustained_drop"   # recent average is much lower than the prior baseline
    missing_data = "missing_data"       # no readings for N consecutive days
    sudden_change = "sudden_change"     # today's value is far outside the recent rolling average


# ---------------------------------------------------------------------
# VitalThreshold
# ---------------------------------------------------------------------

class VitalThreshold(Base):
    __tablename__ = "vital_thresholds"

    id = Column(GUID(), primary_key=True, default=_uuid_default)
    # NULL patient_id = a global default threshold that applies to any
    # patient who does not have their own override for this vital_type.
    patient_id = Column(GUID(), ForeignKey("patients.id", ondelete="CASCADE"), nullable=True)

    vital_type = Column(SAEnum(VitalType), nullable=False)
    severity = Column(SAEnum(Severity), nullable=False)
    min_value = Column(Numeric(8, 2), nullable=True)  # NULL = no lower bound for this band
    max_value = Column(Numeric(8, 2), nullable=True)  # NULL = no upper bound for this band

    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(GUID(), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "min_value IS NOT NULL OR max_value IS NOT NULL",
            name="ck_threshold_has_a_bound",
        ),
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value < max_value",
            name="ck_threshold_min_lt_max",
        ),
        Index("ix_vital_thresholds_patient_type", "patient_id", "vital_type"),
    )

    @validates("min_value", "max_value")
    def validate_bounds(self, key, value):
        return value


# ---------------------------------------------------------------------
# ActivityRule
# ---------------------------------------------------------------------

class ActivityRule(Base):
    __tablename__ = "activity_rules"

    id = Column(GUID(), primary_key=True, default=_uuid_default)
    patient_id = Column(GUID(), ForeignKey("patients.id", ondelete="CASCADE"), nullable=True)  # NULL = global default

    activity_type = Column(SAEnum(ActivityType), nullable=False)
    rule_type = Column(SAEnum(ActivityRuleType), nullable=False)
    severity = Column(SAEnum(Severity), nullable=False)

    # Rule-specific settings, e.g.:
    #   sustained_drop: {"lookback_days": 7, "baseline_days": 14, "drop_pct": 40}
    #   missing_data:   {"missing_days": 2}
    #   sudden_change:  {"window_days": 7, "deviation_pct": 50}
    parameters = Column(JSON, nullable=False, default=dict)

    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(GUID(), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_activity_rules_patient_type", "patient_id", "activity_type"),
    )


# ---------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(GUID(), primary_key=True, default=_uuid_default)
    patient_id = Column(GUID(), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)

    source_type = Column(SAEnum(AlertSourceType), nullable=False)
    # Free-text rule identifier, e.g. "vital_out_of_range", "sustained_drop",
    # "missing_data", "sudden_change" — kept as a string rather than a rigid
    # enum so new rule types can be added without a schema migration.
    rule_type = Column(String(50), nullable=False)

    severity = Column(SAEnum(Severity), nullable=False)
    status = Column(SAEnum(AlertStatus), nullable=False, default=AlertStatus.open)

    message = Column(Text, nullable=False)
    # Structured details for the recipient / for programmatic use: measured
    # value, threshold bounds, rule parameters, etc.
    details = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_occurrence_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    occurrence_count = Column(Integer, nullable=False, default=1)

    acknowledged_by = Column(GUID(), ForeignKey("users.id"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    escalated_at = Column(DateTime(timezone=True), nullable=True)

    resolved_by = Column(GUID(), ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    events = relationship("AlertEvent", back_populates="alert", cascade="all, delete-orphan", order_by="AlertEvent.event_at")
    notifications = relationship("Notification", back_populates="alert", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("occurrence_count >= 1", name="ck_alert_occurrence_count_positive"),
        Index("ix_alerts_patient_status", "patient_id", "status"),
        # Supports the suppression lookup: "is there already an open alert
        # for this patient + rule_type?" — the single most frequent query
        # the alert engine runs, on every single incoming reading.
        Index("ix_alerts_dedup_lookup", "patient_id", "rule_type", "status"),
        Index("ix_alerts_created_at", "created_at"),
    )


# ---------------------------------------------------------------------
# AlertEvent (append-only audit trail)
# ---------------------------------------------------------------------

class AlertEvent(Base):
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(GUID(), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(SAEnum(AlertEventType), nullable=False)
    performed_by = Column(GUID(), ForeignKey("users.id"), nullable=True)  # NULL for system-generated events
    event_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes = Column(Text, nullable=True)

    alert = relationship("Alert", back_populates="events")

    __table_args__ = (
        Index("ix_alert_events_alert_time", "alert_id", "event_at"),
    )


# ---------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------

class Notification(Base):
    __tablename__ = "alert_notifications"

    id = Column(GUID(), primary_key=True, default=_uuid_default)
    alert_id = Column(GUID(), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    channel = Column(SAEnum(NotificationChannel), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(SAEnum(NotificationStatus), nullable=False, default=NotificationStatus.pending)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    failure_reason = Column(String(255), nullable=True)

    alert = relationship("Alert", back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_alert", "alert_id"),
    )
