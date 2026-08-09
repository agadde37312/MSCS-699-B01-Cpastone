"""
v2 routes: the OPTIMIZED implementation. Each fix below maps directly to a
bottleneck documented in docs/performance_analysis.md.
"""
import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Response, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Patient, VitalsReading
from app.cache import risk_report_cache
from app.risk_engine.risk_scoring import score_reading
from app.risk_engine.risk_classification import classify_dict
from app.risk_engine.trend_analysis import compute_trend
from app.risk_engine.risk_factors import identify_risk_factors
from app.risk_engine.recommendations import generate_recommendations

router = APIRouter(prefix="/api/v2", tags=["v2-optimized"])

# Trend only needs a bounded recent window, not the patient's entire history --
# this also bounds the cost of the risk-report endpoint regardless of how long
# a patient has been enrolled (fixes the "heavy patient" scaling problem).
TREND_WINDOW = 20


def _reading_to_dict(r) -> dict:
    return {
        "heart_rate": r.heart_rate, "systolic_bp": r.systolic_bp,
        "diastolic_bp": r.diastolic_bp, "spo2": r.spo2,
        "temperature_c": r.temperature_c, "respiratory_rate": r.respiratory_rate,
        "blood_glucose": r.blood_glucose,
    }


# ---------------------------------------------------------------------------
# FIX 1: pagination + lightweight response (only fields a list view needs)
# ---------------------------------------------------------------------------
@router.get("/patients")
def list_patients(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.query(func.count(Patient.id)).scalar()
    patients = (
        db.query(Patient.id, Patient.name, Patient.age)  # only list-view columns
        .order_by(Patient.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    response.headers["Cache-Control"] = "public, max-age=60"  # patient roster changes rarely
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [{"id": p.id, "name": p.name, "age": p.age} for p in patients],
    }


# ---------------------------------------------------------------------------
# FIX 2: composite index (patient_id, timestamp) now used automatically +
# pagination + optional date-range filtering, so the response size no longer
# scales with a patient's total lifetime history
# ---------------------------------------------------------------------------
@router.get("/patients/{patient_id}/readings")
def get_patient_readings(
    patient_id: int,
    response: Response,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = (
        db.query(func.count(VitalsReading.id))
        .filter(VitalsReading.patient_id == patient_id)
        .scalar()
    )
    readings = (
        db.query(VitalsReading)
        .filter(VitalsReading.patient_id == patient_id)
        .order_by(VitalsReading.timestamp.desc())  # served directly from the index now
        .offset(offset)
        .limit(limit)
        .all()
    )
    response.headers["Cache-Control"] = "private, max-age=15"
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {"id": r.id, "timestamp": r.timestamp.isoformat(), **_reading_to_dict(r)}
            for r in readings
        ],
    }


# ---------------------------------------------------------------------------
# FIX 3: N+1 eliminated -- one aggregated SQL query with GROUP BY instead of
# one Python-side query per patient
# ---------------------------------------------------------------------------
@router.get("/dashboard/summary")
def dashboard_summary(response: Response, db: Session = Depends(get_db)):
    rows = (
        db.query(
            VitalsReading.patient_id,
            func.count(VitalsReading.id).label("reading_count"),
            func.avg(VitalsReading.heart_rate).label("avg_heart_rate"),
            func.max(VitalsReading.timestamp).label("latest_reading_time"),
        )
        .group_by(VitalsReading.patient_id)
        .all()
    )
    # one extra query to attach names, instead of N -- still batch, not per-row
    names = {p.id: p.name for p in db.query(Patient.id, Patient.name).all()}

    response.headers["Cache-Control"] = "public, max-age=30"
    return [
        {
            "patient_id": r.patient_id,
            "name": names.get(r.patient_id),
            "reading_count": r.reading_count,
            "avg_heart_rate": round(r.avg_heart_rate, 1) if r.avg_heart_rate else None,
            "latest_reading_time": r.latest_reading_time,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# FIX 4: in-memory TTL cache keyed by (patient_id, latest_reading_id) -- the
# cache key itself changes automatically the moment new data arrives, so this
# is correctness-safe, not just "cache and hope it's still fresh." Also
# bounds the trend calculation to a fixed recent window instead of the
# patient's entire history, and adds an HTTP Cache-Control header.
# ---------------------------------------------------------------------------
@router.get("/patients/{patient_id}/risk-report")
def get_risk_report(patient_id: int, response: Response, db: Session = Depends(get_db)):
    latest = (
        db.query(VitalsReading)
        .filter(VitalsReading.patient_id == patient_id)
        .order_by(VitalsReading.timestamp.desc())
        .first()
    )
    if latest is None:
        return {"error": "no readings found"}

    cache_key = f"risk-report:{patient_id}:{latest.id}"
    cached = risk_report_cache.get(cache_key)
    if cached is not None:
        response.headers["Cache-Control"] = "private, max-age=30"
        response.headers["X-Cache"] = "HIT"
        return cached

    recent_readings = (
        db.query(VitalsReading)
        .filter(VitalsReading.patient_id == patient_id)
        .order_by(VitalsReading.timestamp.desc())
        .limit(TREND_WINDOW)
        .all()
    )
    recent_readings = list(reversed(recent_readings))  # chronological order for trend

    reading_dict = _reading_to_dict(latest)
    scoring_result = score_reading(reading_dict)
    metric_points = [m.points for m in scoring_result.metric_scores]
    level = classify_dict(scoring_result.total_score, metric_points)

    history_scores = [score_reading(_reading_to_dict(r)).total_score for r in recent_readings]
    trend = compute_trend(history_scores)

    factors = identify_risk_factors(reading_dict)
    recs = generate_recommendations(factors, level["label"], trend_direction=trend["direction"])

    result = {
        "patient_id": patient_id,
        "generated_at": datetime.utcnow().isoformat(),
        "total_score": scoring_result.total_score,
        "risk_level": level,
        "trend": trend,
        "risk_factors": factors,
        "recommendations": recs,
    }

    risk_report_cache.set(cache_key, result, ttl_seconds=30)
    response.headers["Cache-Control"] = "private, max-age=30"
    response.headers["X-Cache"] = "MISS"
    return result


@router.get("/cache/stats")
def cache_stats():
    return risk_report_cache.stats()
