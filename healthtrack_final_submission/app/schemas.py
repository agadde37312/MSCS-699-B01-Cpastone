from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr


class PatientCreate(BaseModel):
    medical_record_number: str
    first_name: str
    last_name: str
    date_of_birth: datetime
    sex: Optional[str] = None
    primary_condition: Optional[str] = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    medical_record_number: str
    first_name: str
    last_name: str
    date_of_birth: datetime
    sex: Optional[str]
    primary_condition: Optional[str]
    is_active: bool


class VitalSignCreate(BaseModel):
    patient_id: int
    device_id: Optional[int] = None
    heart_rate: Optional[float] = None
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    spo2: Optional[float] = None
    temperature: Optional[float] = None
    respiratory_rate: Optional[float] = None
    consciousness_level: Optional[str] = "Alert"


class VitalSignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    recorded_at: datetime
    heart_rate: Optional[float]
    systolic_bp: Optional[float]
    diastolic_bp: Optional[float]
    spo2: Optional[float]
    temperature: Optional[float]
    respiratory_rate: Optional[float]
    consciousness_level: Optional[str]


class RiskAssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    assessed_at: datetime
    news2_score: int
    model_probability: Optional[float]
    risk_level: str
    method: str


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    severity: str
    status: str
    message: str
    created_at: datetime
    escalated_at: Optional[datetime]
    resolved_at: Optional[datetime]


class AlertAcknowledge(BaseModel):
    staff_id: int


class StaffCreate(BaseModel):
    full_name: str
    role: str
    email: EmailStr
