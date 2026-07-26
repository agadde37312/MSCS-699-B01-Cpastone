"""
Data access layer.

Every function here owns exactly one responsibility: talk to the database
for one entity, handle the errors that can happen while doing so, and log
the outcome. Nothing in this module knows about HTTP status codes or
FastAPI — that translation happens in main.py, which keeps this layer
reusable (e.g. from a background worker or a CLI script, not just the API).
"""
from datetime import date, datetime
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app import models
from app.logging_config import access_logger, error_logger


class DuplicateError(Exception):
    """Raised when a uniqueness constraint would be violated (e.g. duplicate MRN)."""


class NotFoundError(Exception):
    """Raised when a requested record does not exist."""


class ValidationFailedError(Exception):
    """Raised when model-level validation rejects the data."""


# ---------------------------------------------------------------------
# User
# ---------------------------------------------------------------------

def create_user(db: Session, *, email: str, hashed_password: str, role: models.UserRole) -> models.User:
    user = models.User(email=email, hashed_password=hashed_password, role=role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        error_logger.error(f"create_user failed — duplicate email {email}: {exc}")
        raise DuplicateError(f"A user with email '{email}' already exists.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"create_user failed: {exc}")
        raise
    db.refresh(user)
    access_logger.info(f"Created user {user.id} ({user.email}, role={user.role.value})")
    return user


def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    return db.execute(select(models.User).where(models.User.email == email.lower().strip())).scalar_one_or_none()


def get_user(db: Session, user_id: UUID) -> Optional[models.User]:
    return db.get(models.User, str(user_id))


# ---------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------

def create_patient(db: Session, **fields) -> models.Patient:
    try:
        patient = models.Patient(**fields)
        db.add(patient)
        db.commit()
    except ValueError as exc:  # raised by @validates on the model
        db.rollback()
        error_logger.warning(f"create_patient validation failed: {exc}")
        raise ValidationFailedError(str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        error_logger.error(f"create_patient failed — duplicate MRN {fields.get('mrn')}: {exc}")
        raise DuplicateError(f"A patient with MRN '{fields.get('mrn')}' already exists.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"create_patient failed: {exc}")
        raise
    db.refresh(patient)
    access_logger.info(f"Created patient {patient.id} (MRN={patient.mrn})")
    return patient


def get_patient(db: Session, patient_id: UUID) -> models.Patient:
    patient = db.get(models.Patient, str(patient_id))
    if patient is None:
        raise NotFoundError(f"No patient found with id '{patient_id}'.")
    return patient


def get_patient_by_mrn(db: Session, mrn: str) -> Optional[models.Patient]:
    return db.execute(select(models.Patient).where(models.Patient.mrn == mrn.strip())).scalar_one_or_none()


def list_patients(
    db: Session, *, care_unit: Optional[str] = None, page: int = 1, page_size: int = 50
) -> tuple[Sequence[models.Patient], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    stmt = select(models.Patient)
    count_stmt = select(func.count()).select_from(models.Patient)
    if care_unit:
        stmt = stmt.where(models.Patient.care_unit == care_unit)
        count_stmt = count_stmt.where(models.Patient.care_unit == care_unit)

    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(models.Patient.last_name, models.Patient.first_name)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = db.execute(stmt).scalars().all()
    return items, total


def update_patient(db: Session, patient_id: UUID, **fields) -> models.Patient:
    patient = get_patient(db, patient_id)
    try:
        for key, value in fields.items():
            if value is not None:
                setattr(patient, key, value)
        db.commit()
    except ValueError as exc:
        db.rollback()
        error_logger.warning(f"update_patient validation failed for {patient_id}: {exc}")
        raise ValidationFailedError(str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"update_patient failed for {patient_id}: {exc}")
        raise
    db.refresh(patient)
    access_logger.info(f"Updated patient {patient_id}")
    return patient


def delete_patient(db: Session, patient_id: UUID) -> None:
    patient = get_patient(db, patient_id)
    try:
        db.delete(patient)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"delete_patient failed for {patient_id}: {exc}")
        raise
    access_logger.info(f"Deleted patient {patient_id}")


# ---------------------------------------------------------------------
# VitalSigns
# ---------------------------------------------------------------------

def create_vital_signs(db: Session, **fields) -> models.VitalSigns:
    # Confirm the patient exists before inserting, so we raise a clear
    # NotFoundError instead of a raw foreign-key IntegrityError.
    get_patient(db, fields["patient_id"])
    try:
        vital = models.VitalSigns(**fields)
        db.add(vital)
        db.commit()
    except ValueError as exc:
        db.rollback()
        error_logger.warning(f"create_vital_signs validation failed: {exc}")
        raise ValidationFailedError(str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"create_vital_signs failed: {exc}")
        raise
    db.refresh(vital)
    access_logger.info(
        f"Recorded vital sign {vital.vital_type.value}={vital.value}{vital.unit} "
        f"for patient {vital.patient_id}"
    )
    return vital


def get_vital_signs(db: Session, vital_id: int) -> models.VitalSigns:
    vital = db.get(models.VitalSigns, vital_id)
    if vital is None:
        raise NotFoundError(f"No vital sign reading found with id '{vital_id}'.")
    return vital


def list_vital_signs(
    db: Session,
    *,
    patient_id: UUID,
    vital_type: Optional[models.VitalType] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[Sequence[models.VitalSigns], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 500)

    stmt = select(models.VitalSigns).where(models.VitalSigns.patient_id == str(patient_id))
    count_stmt = select(func.count()).select_from(models.VitalSigns).where(
        models.VitalSigns.patient_id == str(patient_id)
    )

    if vital_type is not None:
        stmt = stmt.where(models.VitalSigns.vital_type == vital_type)
        count_stmt = count_stmt.where(models.VitalSigns.vital_type == vital_type)
    if start_date is not None:
        stmt = stmt.where(models.VitalSigns.recorded_at >= start_date)
        count_stmt = count_stmt.where(models.VitalSigns.recorded_at >= start_date)
    if end_date is not None:
        stmt = stmt.where(models.VitalSigns.recorded_at <= end_date)
        count_stmt = count_stmt.where(models.VitalSigns.recorded_at <= end_date)

    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(models.VitalSigns.recorded_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = db.execute(stmt).scalars().all()
    return items, total


def delete_vital_signs(db: Session, vital_id: int) -> None:
    vital = get_vital_signs(db, vital_id)
    try:
        db.delete(vital)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"delete_vital_signs failed for {vital_id}: {exc}")
        raise
    access_logger.info(f"Deleted vital sign reading {vital_id}")


# ---------------------------------------------------------------------
# ActivityData
# ---------------------------------------------------------------------

def create_activity_data(db: Session, **fields) -> models.ActivityData:
    get_patient(db, fields["patient_id"])
    try:
        activity = models.ActivityData(**fields)
        db.add(activity)
        db.commit()
    except ValueError as exc:
        db.rollback()
        error_logger.warning(f"create_activity_data validation failed: {exc}")
        raise ValidationFailedError(str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        error_logger.error(f"create_activity_data failed — duplicate daily entry: {exc}")
        raise DuplicateError(
            "An activity entry of this type already exists for this patient on this date."
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"create_activity_data failed: {exc}")
        raise
    db.refresh(activity)
    access_logger.info(
        f"Recorded activity {activity.activity_type.value}={activity.value}{activity.unit} "
        f"for patient {activity.patient_id} on {activity.recorded_date}"
    )
    return activity


def get_activity_data(db: Session, activity_id: int) -> models.ActivityData:
    activity = db.get(models.ActivityData, activity_id)
    if activity is None:
        raise NotFoundError(f"No activity entry found with id '{activity_id}'.")
    return activity


def list_activity_data(
    db: Session,
    *,
    patient_id: UUID,
    activity_type: Optional[models.ActivityType] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[Sequence[models.ActivityData], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 500)

    stmt = select(models.ActivityData).where(models.ActivityData.patient_id == str(patient_id))
    count_stmt = select(func.count()).select_from(models.ActivityData).where(
        models.ActivityData.patient_id == str(patient_id)
    )

    if activity_type is not None:
        stmt = stmt.where(models.ActivityData.activity_type == activity_type)
        count_stmt = count_stmt.where(models.ActivityData.activity_type == activity_type)
    if start_date is not None:
        stmt = stmt.where(models.ActivityData.recorded_date >= start_date)
        count_stmt = count_stmt.where(models.ActivityData.recorded_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(models.ActivityData.recorded_date <= end_date)
        count_stmt = count_stmt.where(models.ActivityData.recorded_date <= end_date)

    total = db.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(models.ActivityData.recorded_date.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = db.execute(stmt).scalars().all()
    return items, total


def delete_activity_data(db: Session, activity_id: int) -> None:
    activity = get_activity_data(db, activity_id)
    try:
        db.delete(activity)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        error_logger.error(f"delete_activity_data failed for {activity_id}: {exc}")
        raise
    access_logger.info(f"Deleted activity entry {activity_id}")
