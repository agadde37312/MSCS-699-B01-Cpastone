"""
Database models for the HealthTrack performance testbed.

This is a representative reconstruction of the HealthTrack backend's data layer
(Patient, VitalsReading, RiskReport) built specifically for this performance
evaluation, since the original FastAPI backend/dashboard project files from
earlier development phases are not present in this environment. The schema
matches the entities described in the HealthTrack capstone architecture
(patients, vitals readings, computed risk reports) so the bottlenecks and
optimizations demonstrated here are representative of the real system.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    age = Column(Integer, nullable=False)
    diabetes = Column(Boolean, default=False)
    hypertension = Column(Boolean, default=False)
    copd = Column(Boolean, default=False)
    heart_disease = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    readings = relationship("VitalsReading", back_populates="patient")


class VitalsReading(Base):
    __tablename__ = "vitals_readings"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False)

    heart_rate = Column(Float)
    systolic_bp = Column(Float)
    diastolic_bp = Column(Float)
    spo2 = Column(Float)
    temperature_c = Column(Float)
    respiratory_rate = Column(Float)
    blood_glucose = Column(Float)

    patient = relationship("Patient", back_populates="readings")

    # NOTE: intentionally NOT indexed at first -- this is the "before" schema.
    # See migrations/add_indexes.py for the optimization applied later.


class RiskReport(Base):
    __tablename__ = "risk_reports"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    reading_id = Column(Integer, ForeignKey("vitals_readings.id"), nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow)

    total_score = Column(Integer)
    risk_level = Column(String(20))
    report_json = Column(Text)  # full JSON payload (rule scores, factors, recommendations)
