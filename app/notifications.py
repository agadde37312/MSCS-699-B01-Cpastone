"""
Notification dispatch.

Channels are implemented as small adapter classes behind a common
interface (NotificationChannel.send), so swapping a simulated channel for
a real provider (e.g. Twilio for SMS, SendGrid/SES for email) in
production means writing one new adapter class — nothing else in the
alert system needs to change. See docs/System_Integration_Guide.docx for
exactly how to plug in a real provider.

In this environment, email and SMS are simulated (logged, not actually
sent over a network) since no outbound mail/SMS provider is configured;
in_app notifications are "real" in the sense that they are actually
persisted and readable through the API.
"""
import abc
from datetime import datetime, timezone
from typing import Dict, Any

from sqlalchemy.orm import Session

from app import models
from app.alert_models import Alert, Notification, NotificationChannel as ChannelEnum, NotificationStatus
from app.logging_config import access_logger, error_logger


def build_alert_payload(alert: Alert, patient: models.Patient) -> Dict[str, Any]:
    """Build the structured notification payload for one alert. This same
    payload shape is used across all channels; each channel adapter
    decides how to render it (e.g. the email adapter would turn this into
    a subject + body, the SMS adapter into a short text message)."""
    return {
        "alert_id": str(alert.id),
        "patient_id": str(alert.patient_id),
        "patient_name": f"{patient.first_name} {patient.last_name}",
        "patient_mrn": patient.mrn,
        "severity": alert.severity.value,
        "rule_type": alert.rule_type,
        "message": alert.message,
        "details": alert.details,
        "occurrence_count": alert.occurrence_count,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


class BaseChannel(abc.ABC):
    channel_enum: ChannelEnum

    @abc.abstractmethod
    def send(self, payload: Dict[str, Any]) -> bool:
        """Attempt delivery. Returns True on success, False on failure.
        Must never raise — dispatch_alert() treats any exception here as
        a failed send for that one channel/recipient, without blocking
        delivery to other recipients or channels."""
        ...


class InAppChannel(BaseChannel):
    channel_enum = ChannelEnum.in_app

    def send(self, payload: Dict[str, Any]) -> bool:
        # "Sending" an in-app notification just means it now exists in the
        # database, ready to be fetched by GET /notifications — there is
        # no external system to call.
        return True


class EmailChannel(BaseChannel):
    channel_enum = ChannelEnum.email

    def send(self, payload: Dict[str, Any]) -> bool:
        # SIMULATED: no real SMTP/SendGrid/SES integration is configured
        # in this environment. In production this method would call a
        # real provider's API and return its actual success/failure.
        access_logger.info(
            f"[SIMULATED EMAIL] To recipient for alert {payload['alert_id']} "
            f"({payload['severity']}): {payload['message']}"
        )
        return True


class SMSChannel(BaseChannel):
    channel_enum = ChannelEnum.sms

    def send(self, payload: Dict[str, Any]) -> bool:
        # SIMULATED: no real SMS provider (e.g. Twilio) is configured.
        short_message = f"[HealthTrack] {payload['severity'].upper()} alert: {payload['message'][:120]}"
        access_logger.info(f"[SIMULATED SMS] For alert {payload['alert_id']}: {short_message}")
        return True


CHANNEL_ADAPTERS = {
    ChannelEnum.in_app: InAppChannel(),
    ChannelEnum.email: EmailChannel(),
    ChannelEnum.sms: SMSChannel(),
}


# Which channels are used for each severity. Low/medium severity alerts
# stay in-app only (reduces noise); high/critical also go out over email;
# critical additionally goes over SMS, on the assumption that a critical
# alert needs to reach someone even if they are not currently looking at
# the app or their email.
CHANNELS_BY_SEVERITY = {
    "low": [ChannelEnum.in_app],
    "medium": [ChannelEnum.in_app],
    "high": [ChannelEnum.in_app, ChannelEnum.email],
    "critical": [ChannelEnum.in_app, ChannelEnum.email, ChannelEnum.sms],
}


def get_recipients(db: Session, patient: models.Patient) -> list[models.User]:
    """Who should be notified about this patient's alerts.

    For this phase, recipients are: every active clinician/admin user
    (standing in for "this patient's assigned care team", since Phase 3
    did not implement a clinician-patient panel assignment table), plus
    the patient's own linked user account if they have one, so patients
    can see their own alerts in the mobile app.
    """
    from sqlalchemy import select
    recipients = list(db.execute(
        select(models.User).where(
            models.User.role.in_([models.UserRole.clinician, models.UserRole.admin]),
            models.User.is_active == True,  # noqa: E712
        )
    ).scalars().all())

    if patient.user_id:
        patient_user = db.get(models.User, patient.user_id)
        if patient_user and patient_user.is_active:
            recipients.append(patient_user)

    return recipients


def dispatch_alert(db: Session, alert: Alert, escalation: bool = False) -> list[Notification]:
    """Create and 'send' one Notification per (recipient, channel) pair
    appropriate for this alert's severity, then persist the results.

    Called both when an alert is first created, and again (with
    escalation=True) when an unacknowledged alert is escalated, so
    escalation actually results in a fresh round of notifications rather
    than just a silent status change.
    """
    patient = db.get(models.Patient, alert.patient_id)
    if patient is None:
        error_logger.error(f"dispatch_alert: patient {alert.patient_id} not found for alert {alert.id}")
        return []

    payload = build_alert_payload(alert, patient)
    if escalation:
        payload["escalated"] = True

    channels = CHANNELS_BY_SEVERITY.get(alert.severity.value, [ChannelEnum.in_app])
    recipients = get_recipients(db, patient)

    created: list[Notification] = []
    for recipient in recipients:
        for channel_enum in channels:
            adapter = CHANNEL_ADAPTERS[channel_enum]
            notification = Notification(
                alert_id=alert.id,
                user_id=recipient.id,
                channel=channel_enum,
                payload=payload,
                status=NotificationStatus.pending,
            )
            db.add(notification)
            db.flush()  # assign an id without committing yet

            try:
                success = adapter.send(payload)
            except Exception as exc:  # a channel must never be able to crash the request
                error_logger.error(f"Notification send failed for alert {alert.id} via {channel_enum.value}: {exc}")
                success = False

            if success:
                notification.status = NotificationStatus.sent
                notification.sent_at = datetime.now(timezone.utc)
            else:
                notification.status = NotificationStatus.failed
                notification.failure_reason = "Channel adapter reported failure"

            created.append(notification)

    db.commit()
    access_logger.info(
        f"Dispatched {len(created)} notification(s) for alert {alert.id} "
        f"(severity={alert.severity.value}, escalation={escalation})"
    )
    return created
