"""
v1 routes: the BASELINE / unoptimized implementation, used to establish
the performance report's "before" measurements. Each endpoint below has an
intentional, realistic performance issue, documented inline. These are common,
easy-to-miss mistakes -- not contrived worst-case code.
"""
import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Patient, VitalsReading
from app.risk_engine.risk_scoring import score_reading
from app.risk_engine.risk_classification import classify_dict
from app.risk_engine.trend_analysis import compute_trend
from app.risk_engine.risk_factors import identify_risk_factors
from app.risk_engine.recommendations import generate_recommendations

router = APIRouter(prefix="/api/v1", tags=["v1-baseline"])


def _reading_to_dict(r: VitalsReading) -> dict:
    return {
        "heart_rate": r.heart_rate, "systolic_bp": r.systolic_bp,
        "diastolic_bp": r.diastolic_bp, "spo2": r.spo2,
        "temperature_c": r.temperature_c, "respiratory_rate": r.respiratory_rate,
        "blood_glucose": r.blood_glucose,
    }


# ---------------------------------------------------------------------------
# BOTTLENECK 1: no pagination, returns every column for every patient
# ---------------------------------------------------------------------------
@router.get("/patients")
def list_patients(db: Session = Depends(get_db)):
    patients = db.query(Patient).all()  # entire table, every request
    return [
        {
            "id": p.id, "name": p.name, "age": p.age,
            "diabetes": p.diabetes, "hypertension": p.hypertension,
            "copd": p.copd, "heart_disease": p.heart_disease,
            "created_at": p.created_at.isoformat(),
        }
        for p in patients
    ]


# ---------------------------------------------------------------------------
# BOTTLENECK 2: unpaginated, unindexed ORDER BY on a large table
# ---------------------------------------------------------------------------
@router.get("/patients/{patient_id}/readings")
def get_patient_readings(patient_id: int, db: Session = Depends(get_db)):
    readings = (
        db.query(VitalsReading)
        .filter(VitalsReading.patient_id == patient_id)
        .order_by(VitalsReading.timestamp.desc())
        .all()  # no LIMIT -- every reading this patient has ever had
    )
    return [
        {"id": r.id, "timestamp": r.timestamp.isoformat(), **_reading_to_dict(r)}
        for r in readings
    ]


# ---------------------------------------------------------------------------
# BOTTLENECK 3: classic N+1 query pattern -- one query per patient in a loop
# ---------------------------------------------------------------------------
@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    patients = db.query(Patient).all()  # query 1
    summary = []
    for p in patients:  # then N more queries, one per patient
        readings = (
            db.query(VitalsReading)
            .filter(VitalsReading.patient_id == p.id)
            .all()
        )
        if not readings:
            continue
        avg_hr = sum(r.heart_rate for r in readings) / len(readings)
        latest = max(readings, key=lambda r: r.timestamp)
        summary.append({
            "patient_id": p.id,
            "name": p.name,
            "reading_count": len(readings),
            "avg_heart_rate": round(avg_hr, 1),
            "latest_reading_time": latest.timestamp.isoformat(),
        })
    return summary


# ---------------------------------------------------------------------------
# BOTTLENECK 4: full risk pipeline recomputed from scratch on every request,
# including re-fetching and re-scoring the patient's entire reading history
# for the trend calculation -- no caching at all
# ---------------------------------------------------------------------------
@router.get("/patients/{patient_id}/risk-report")
def get_risk_report(patient_id: int, db: Session = Depends(get_db)):
    readings = (
        db.query(VitalsReading)
        .filter(VitalsReading.patient_id == patient_id)
        .order_by(VitalsReading.timestamp.asc())
        .all()  # entire history, every time, just to get a trend
    )
    if not readings:
        return {"error": "no readings found"}

    latest = readings[-1]
    reading_dict = _reading_to_dict(latest)

    scoring_result = score_reading(reading_dict)
    metric_points = [m.points for m in scoring_result.metric_scores]
    level = classify_dict(scoring_result.total_score, metric_points)

    history_scores = [score_reading(_reading_to_dict(r)).total_score for r in readings]
    trend = compute_trend(history_scores)

    factors = identify_risk_factors(reading_dict)
    recs = generate_recommendations(factors, level["label"], trend_direction=trend["direction"])

    return {
        "patient_id": patient_id,
        "generated_at": datetime.utcnow().isoformat(),
        "total_score": scoring_result.total_score,
        "risk_level": level,
        "trend": trend,
        "risk_factors": factors,
        "recommendations": recs,
    }
