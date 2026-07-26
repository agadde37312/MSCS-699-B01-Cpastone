"""
Author: Arun Bhaskar Gadde

Pydantic schemas (the API's request/response contracts).

These mirror the SQLAlchemy models in models.py but are kept as separate
classes on purpose: the API layer should be free to accept/return a
slightly different shape than the database layer (e.g. hiding
hashed_password entirely, or accepting a plain "password" field that never
touches the database directly).
"""
from datetime import datetime, date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict

from app.models import UserRole, Sex, VitalType, ActivityType, VITAL_RANGES


# ---------------------------------------------------------------------
# User / Auth
# ---------------------------------------------------------------------

class UserCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"email": "j.okafor@hospital.example.com", "password": "SecurePass123", "role": "clinician"}
    })
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = UserRole.patient

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter.")
        return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"email": "j.okafor@hospital.example.com", "password": "SecurePass123"}
    })
    email: EmailStr
    password: str


# ---------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------

class PatientBase(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "mrn": "MRN-100245",
            "first_name": "Maria",
            "last_name": "Chen",
            "date_of_birth": "1958-03-14",
            "sex": "female",
            "primary_diagnosis": "Type 2 Diabetes, Hypertension",
            "care_unit": "Pilot Unit A",
            "emergency_contact_name": "David Chen",
            "emergency_contact_phone": "+1-555-0303",
        }
    })
    mrn: str = Field(..., min_length=1, max_length=50)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    date_of_birth: date
    sex: Sex = Sex.unspecified
    primary_diagnosis: Optional[str] = Field(None, max_length=255)
    care_unit: Optional[str] = Field(None, max_length=100)
    emergency_contact_name: Optional[str] = Field(None, max_length=150)
    emergency_contact_phone: Optional[str] = Field(None, max_length=20)

    @field_validator("date_of_birth")
    @classmethod
    def dob_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("date_of_birth cannot be in the future.")
        return v

    @field_validator("mrn")
    @classmethod
    def mrn_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("mrn cannot be blank.")
        return v.strip()


class PatientCreate(PatientBase):
    user_id: Optional[UUID] = None


class PatientUpdate(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    primary_diagnosis: Optional[str] = Field(None, max_length=255)
    care_unit: Optional[str] = Field(None, max_length=100)
    emergency_contact_name: Optional[str] = Field(None, max_length=150)
    emergency_contact_phone: Optional[str] = Field(None, max_length=20)


class PatientOut(PatientBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------
# VitalSigns
# ---------------------------------------------------------------------

class VitalSignsBase(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "vital_type": "blood_pressure_systolic",
            "value": 148,
            "unit": "mmHg",
            "recorded_at": "2026-07-14T14:03:00Z",
            "source_device": "SN-VS200-88231",
        }
    })
    vital_type: VitalType
    value: float
    unit: str = Field(..., max_length=15)
    recorded_at: datetime
    source_device: Optional[str] = Field(None, max_length=100)

    @field_validator("value")
    @classmethod
    def value_in_range(cls, v: float, info) -> float:
        vtype = info.data.get("vital_type")
        if vtype is not None and vtype in VITAL_RANGES:
            lo, hi = VITAL_RANGES[vtype]
            if not (lo <= v <= hi):
                raise ValueError(
                    f"{vtype.value} value {v} is outside the plausible range [{lo}, {hi}]."
                )
        return v

    @field_validator("recorded_at")
    @classmethod
    def not_future(cls, v: datetime) -> datetime:
        # Allow a small clock-skew grace window (5 minutes) for device clocks
        # that may be slightly ahead of the server.
        from datetime import timezone, timedelta
        now = datetime.now(timezone.utc)
        v_cmp = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if v_cmp > now + timedelta(minutes=5):
            raise ValueError("recorded_at cannot be in the future.")
        return v


class VitalSignsCreate(VitalSignsBase):
    patient_id: UUID


class VitalSignsOut(VitalSignsBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: UUID
    received_at: datetime
    is_flagged: bool


# ---------------------------------------------------------------------
# ActivityData
# ---------------------------------------------------------------------

class ActivityDataBase(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "activity_type": "steps",
            "value": 8500,
            "unit": "steps",
            "recorded_date": "2026-07-14",
            "source_device": "SN-VS200-88231",
        }
    })
    activity_type: ActivityType
    value: float = Field(..., ge=0)
    unit: str = Field(..., max_length=20)
    recorded_date: date
    source_device: Optional[str] = Field(None, max_length=100)

    @field_validator("recorded_date")
    @classmethod
    def date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("recorded_date cannot be in the future.")
        return v


class ActivityDataCreate(ActivityDataBase):
    patient_id: UUID


class ActivityDataOut(ActivityDataBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: UUID
    created_at: datetime


# ---------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------

class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list


class ErrorResponse(BaseModel):
    error_code: str
    message: str
