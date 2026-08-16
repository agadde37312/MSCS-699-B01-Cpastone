"""
HealthTrack SQLAlchemy models.

This module implements the core, working subset of the full 20-entity
HealthTrack schema designed in the earlier database design deliverable
(see docs/ERD from that phase). The subset below covers every entity
required for the end-to-end flows exercised by this final integration:
patient management, vitals ingestion, risk assessment, and alerting.
Supporting/reference entities from the full ERD (e.g., audit logs,
insurance, department hierarchy) are represented in the design doc but
are intentionally out of scope for this runtime build.
"""
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Boolean,
    Enum, Text, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class RiskLevel(str, PyEnum):
    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"
    CRITICAL = "Critical"


class AlertStatus(str, PyEnum):
    OPEN = "Open"
    ACKNOWLEDGED = "Acknowledged"
    ESCALATED = "Escalated"
    RESOLVED = "Resolved"


class AlertSeverity(str, PyEnum):
    INFO = "Info"
    WARNING = "Warning"
    CRITICAL = "Critical"


class StaffRole(str, PyEnum):
    NURSE = "Nurse"
    PHYSICIAN = "Physician"
    ADMIN = "Admin"
    TAM = "TAM"


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    medical_record_number = Column(String(32), unique=True, nullable=False, index=True)
    first_name = Column(String(64), nullable=False)
    last_name = Column(String(64), nullable=False)
    date_of_birth = Column(DateTime, nullable=False)
    sex = Column(String(16), nullable=True)
    primary_condition = Column(String(128), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    vitals = relationship("VitalSign", back_populates="patient", cascade="all, delete-orphan")
    assessments = relationship("RiskAssessment", back_populates="patient", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="patient", cascade="all, delete-orphan")
    devices = relationship("Device", back_populates="patient")


class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True)
    full_name = Column(String(128), nullable=False)
    role = Column(Enum(StaffRole), nullable=False, default=StaffRole.NURSE)
    email = Column(String(128), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    acknowledged_alerts = relationship("Alert", back_populates="acknowledged_by")


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    device_serial = Column(String(64), unique=True, nullable=False)
    device_type = Column(String(64), nullable=False)  # e.g. "pulse_ox", "bp_cuff"
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    last_seen_at = Column(DateTime, nullable=True)

    patient = relationship("Patient", back_populates="devices")


class VitalSign(Base):
    __tablename__ = "vital_signs"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    heart_rate = Column(Float, nullable=True)
    systolic_bp = Column(Float, nullable=True)
    diastolic_bp = Column(Float, nullable=True)
    spo2 = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    respiratory_rate = Column(Float, nullable=True)
    consciousness_level = Column(String(16), nullable=True)  # AVPU: Alert/Voice/Pain/Unresponsive

    patient = relationship("Patient", back_populates="vitals")

    __table_args__ = (
        Index("ix_vitals_patient_time", "patient_id", "recorded_at"),
    )


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    vital_sign_id = Column(Integer, ForeignKey("vital_signs.id"), nullable=True)
    assessed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    news2_score = Column(Integer, nullable=False)
    model_probability = Column(Float, nullable=True)  # Random Forest predicted probability of High risk
    risk_level = Column(Enum(RiskLevel), nullable=False)
    method = Column(String(32), default="news2+rf")  # scoring method used

    patient = relationship("Patient", back_populates="assessments")

    __table_args__ = (
        Index("ix_assessment_patient_time", "patient_id", "assessed_at"),
    )


class PatientNote(Base):
    """Free-text clinical notes a staff member can attach to a patient
    (e.g., context for an alert, handover notes). Added as the example
    schema change walked through in the maintenance guide.
    """
    __tablename__ = "patient_notes"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    note_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient")
    staff = relationship("Staff")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    risk_assessment_id = Column(Integer, ForeignKey("risk_assessments.id"), nullable=True)
    severity = Column(Enum(AlertSeverity), nullable=False)
    status = Column(Enum(AlertStatus), default=AlertStatus.OPEN, nullable=False)
    message = Column(Text, nullable=False)
    dedup_key = Column(String(128), nullable=False, index=True)  # patient+reason bucket for deduplication
    created_at = Column(DateTime, default=datetime.utcnow)
    escalated_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    acknowledged_by_id = Column(Integer, ForeignKey("staff.id"), nullable=True)

    patient = relationship("Patient", back_populates="alerts")
    acknowledged_by = relationship("Staff", back_populates="acknowledged_alerts")

    __table_args__ = (
        Index("ix_alerts_status_severity", "status", "severity"),
    )
