"""
SQLAlchemy models for HealthTrack Phase 3.

Scope note: Phase 2 defined a 20-entity data layer for the full system. Phase
3 implements the backend for the three entities the assignment specifies
(Patient, VitalSigns, ActivityData), plus a minimal User model required to
support authentication/authorization. Naming and constraint conventions
(UUID identity keys, per-vital_type CHECK ranges, timestamp columns) are
kept consistent with the Phase 2 ERD and schema.sql so this can be merged
back into the full data layer in a later phase without a redesign.
"""
import enum
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Numeric,
    CheckConstraint, UniqueConstraint, Index, Enum as SAEnum, BigInteger, Integer, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.database import Base


def _uuid_default():
    return str(uuid.uuid4())


# SQLite only supports autoincrementing primary keys when the column type is
# exactly INTEGER (its rowid alias); BIGINT works fine for the same purpose
# on Postgres (BIGSERIAL). This variant type lets the same model definition
# work correctly against both, which matters because the automated test
# suite runs against SQLite while staging/production run Postgres.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


# A UUID type that stores as native UUID on Postgres but as a plain string
# on SQLite, so the same models work against both (SQLite is used in the
# automated test suite; Postgres is used in staging/production).
from sqlalchemy.types import TypeDecorator, CHAR
import uuid as _uuid_mod


class GUID(TypeDecorator):
    """Platform-independent UUID column."""
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID())
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, _uuid_mod.UUID):
            return str(_uuid_mod.UUID(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return str(value)


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------

class UserRole(str, enum.Enum):
    patient = "patient"
    clinician = "clinician"
    admin = "admin"


class Sex(str, enum.Enum):
    female = "female"
    male = "male"
    other = "other"
    unspecified = "unspecified"


class VitalType(str, enum.Enum):
    heart_rate = "heart_rate"
    blood_pressure_systolic = "blood_pressure_systolic"
    blood_pressure_diastolic = "blood_pressure_diastolic"
    spo2 = "spo2"
    blood_glucose = "blood_glucose"
    temperature = "temperature"


class ActivityType(str, enum.Enum):
    steps = "steps"
    calories_burned = "calories_burned"
    active_minutes = "active_minutes"
    sleep_hours = "sleep_hours"
    distance_km = "distance_km"


# Physiologically plausible ranges, enforced both at the DB layer (CHECK
# constraints below) and the API layer (Pydantic validators in schemas.py).
# Keeping the same bounds in both places is intentional defense in depth:
# the API layer gives a fast, friendly error; the DB layer is the backstop
# that protects data integrity even if a bug bypasses the API.
VITAL_RANGES = {
    VitalType.heart_rate: (20, 300),
    VitalType.blood_pressure_systolic: (40, 300),
    VitalType.blood_pressure_diastolic: (20, 200),
    VitalType.spo2: (0, 100),
    VitalType.blood_glucose: (10, 900),
    VitalType.temperature: (25, 45),  # Celsius
}


# ---------------------------------------------------------------------
# User (minimal identity/auth model)
# ---------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(GUID(), primary_key=True, default=_uuid_default)
    email = Column(String(255), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole), nullable=False, default=UserRole.patient)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    patient_profile = relationship("Patient", back_populates="user", uselist=False)

    @validates("email")
    def validate_email(self, key, value):
        if not value or "@" not in value:
            raise ValueError("A valid email address is required.")
        return value.lower().strip()

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


# ---------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------

class Patient(Base):
    __tablename__ = "patients"

    id = Column(GUID(), primary_key=True, default=_uuid_default)
    user_id = Column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True)

    mrn = Column(String(50), nullable=False, unique=True, index=True)  # Medical Record Number
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    sex = Column(SAEnum(Sex), nullable=False, default=Sex.unspecified)
    primary_diagnosis = Column(String(255), nullable=True)
    care_unit = Column(String(100), nullable=True)
    emergency_contact_name = Column(String(150), nullable=True)
    emergency_contact_phone = Column(String(20), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="patient_profile")
    vital_signs = relationship("VitalSigns", back_populates="patient", cascade="all, delete-orphan")
    activity_data = relationship("ActivityData", back_populates="patient", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("date_of_birth <= CURRENT_DATE", name="ck_patient_dob_past"),
        CheckConstraint("length(trim(first_name)) > 0", name="ck_patient_first_name_not_blank"),
        CheckConstraint("length(trim(last_name)) > 0", name="ck_patient_last_name_not_blank"),
        Index("ix_patients_care_unit", "care_unit"),
    )

    @validates("date_of_birth")
    def validate_dob(self, key, value):
        if value is None:
            raise ValueError("date_of_birth is required.")
        if isinstance(value, datetime):
            value = value.date()
        if value > date.today():
            raise ValueError("date_of_birth cannot be in the future.")
        return value

    @validates("mrn")
    def validate_mrn(self, key, value):
        if not value or not value.strip():
            raise ValueError("mrn (Medical Record Number) cannot be blank.")
        return value.strip()

    def __repr__(self):
        return f"<Patient {self.mrn} {self.first_name} {self.last_name}>"


# ---------------------------------------------------------------------
# VitalSigns
# ---------------------------------------------------------------------

class VitalSigns(Base):
    __tablename__ = "vital_signs"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    patient_id = Column(GUID(), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)

    vital_type = Column(SAEnum(VitalType), nullable=False)
    value = Column(Numeric(8, 2), nullable=False)
    unit = Column(String(15), nullable=False)

    recorded_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_flagged = Column(Boolean, nullable=False, default=False)
    source_device = Column(String(100), nullable=True)

    patient = relationship("Patient", back_populates="vital_signs")

    __table_args__ = (
        CheckConstraint(
            "("
            "(vital_type = 'heart_rate' AND value BETWEEN 20 AND 300) OR "
            "(vital_type = 'blood_pressure_systolic' AND value BETWEEN 40 AND 300) OR "
            "(vital_type = 'blood_pressure_diastolic' AND value BETWEEN 20 AND 200) OR "
            "(vital_type = 'spo2' AND value BETWEEN 0 AND 100) OR "
            "(vital_type = 'blood_glucose' AND value BETWEEN 10 AND 900) OR "
            "(vital_type = 'temperature' AND value BETWEEN 25 AND 45)"
            ")",
            name="ck_vital_value_in_range",
        ),
        # Index supporting the most common query pattern: "give me this
        # patient's readings, most recent first, optionally filtered by type"
        Index("ix_vitals_patient_recorded", "patient_id", "recorded_at"),
        Index("ix_vitals_patient_type_recorded", "patient_id", "vital_type", "recorded_at"),
    )

    @validates("value")
    def validate_value(self, key, value):
        # Defense in depth: re-check the range in Python too, so an
        # ORM-level .add()/.flush() fails fast with a clear message instead
        # of surfacing a raw IntegrityError from the database.
        vtype = self.vital_type
        if vtype is not None and vtype in VITAL_RANGES:
            lo, hi = VITAL_RANGES[vtype]
            if value is None:
                raise ValueError("value is required.")
            if not (lo <= float(value) <= hi):
                raise ValueError(
                    f"{vtype.value} value {value} is out of the plausible range [{lo}, {hi}]."
                )
        return value

    @validates("recorded_at")
    def validate_recorded_at(self, key, value):
        if value is None:
            raise ValueError("recorded_at is required.")
        return value

    def __repr__(self):
        return f"<VitalSigns {self.vital_type}={self.value}{self.unit} @ {self.recorded_at}>"


# ---------------------------------------------------------------------
# ActivityData
# ---------------------------------------------------------------------

class ActivityData(Base):
    __tablename__ = "activity_data"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    patient_id = Column(GUID(), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)

    activity_type = Column(SAEnum(ActivityType), nullable=False)
    value = Column(Numeric(10, 2), nullable=False)
    unit = Column(String(20), nullable=False)
    recorded_date = Column(Date, nullable=False)
    source_device = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    patient = relationship("Patient", back_populates="activity_data")

    __table_args__ = (
        CheckConstraint("value >= 0", name="ck_activity_value_non_negative"),
        # One aggregate reading per patient, per activity type, per day —
        # prevents accidental duplicate daily syncs from a wearable device.
        UniqueConstraint("patient_id", "activity_type", "recorded_date", name="uq_activity_patient_type_date"),
        Index("ix_activity_patient_date", "patient_id", "recorded_date"),
    )

    @validates("value")
    def validate_value(self, key, value):
        if value is None:
            raise ValueError("value is required.")
        if float(value) < 0:
            raise ValueError("Activity value cannot be negative.")
        return value

    @validates("recorded_date")
    def validate_recorded_date(self, key, value):
        if value is None:
            raise ValueError("recorded_date is required.")
        if isinstance(value, datetime):
            value = value.date()
        if value > date.today():
            raise ValueError("recorded_date cannot be in the future.")
        return value

    def __repr__(self):
        return f"<ActivityData {self.activity_type}={self.value}{self.unit} on {self.recorded_date}>"
