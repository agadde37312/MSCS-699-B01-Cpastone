"""
Pydantic request/response schemas for the Phase 4 alert API: thresholds,
alerts (list/detail/actions), and notifications.

Kept in a separate module from app/schemas.py (Phase 3) since these wrap
app/alert_models.py rather than app/models.py, mirroring how alert_models.py
itself is kept separate from models.py.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.alert_models import AlertEventType, AlertStatus, NotificationChannel, NotificationStatus, Severity
from app.models import VitalType


# ---------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------

class ThresholdCreateRequest(BaseModel):
    vital_type: VitalType
    severity: Severity
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class ThresholdResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    vital_type: VitalType
    severity: Severity
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    is_active: bool


# ---------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------

class AlertEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: AlertEventType
    performed_by: Optional[str] = None
    event_at: datetime
    notes: Optional[str] = None


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str
    rule_type: str
    severity: Severity
    status: AlertStatus
    message: str
    details: Dict[str, Any]
    occurrence_count: int
    created_at: datetime
    last_occurrence_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None


class AlertDetailResponse(AlertResponse):
    events: List[AlertEventResponse] = []


class AlertListResponse(BaseModel):
    items: List[AlertResponse]
    total: int


class AlertActionRequest(BaseModel):
    notes: Optional[str] = None


class EscalationCheckResponse(BaseModel):
    escalated_count: int


# ---------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------

class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    alert_id: str
    channel: NotificationChannel
    status: NotificationStatus
    created_at: datetime
    sent_at: Optional[datetime] = None
