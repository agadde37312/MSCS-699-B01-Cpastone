import asyncio
import json
from datetime import datetime
from typing import List

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app import models, schemas, risk_engine, alerts
from app.database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="HealthTrack API",
    description="Patient monitoring, risk assessment, and alerting API",
    version="1.0.0",
)


# --------------------------------------------------------------------
# WebSocket connection manager for real-time dashboard updates
# --------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message, default=str))
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)


manager = ConnectionManager()


@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive / ignore client pings
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# --------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------
@app.get("/health")
def health_check():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


# --------------------------------------------------------------------
# Patients
# --------------------------------------------------------------------
@app.post("/patients", response_model=schemas.PatientOut, status_code=201)
def create_patient(patient: schemas.PatientCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Patient).filter_by(
        medical_record_number=patient.medical_record_number
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="MRN already exists")
    db_patient = models.Patient(**patient.model_dump())
    db.add(db_patient)
    db.commit()
    db.refresh(db_patient)
    return db_patient


@app.get("/patients", response_model=List[schemas.PatientOut])
def list_patients(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    return db.query(models.Patient).offset(skip).limit(limit).all()


@app.get("/patients/{patient_id}", response_model=schemas.PatientOut)
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(models.Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


# --------------------------------------------------------------------
# Vitals ingestion -> triggers risk assessment -> triggers alerting
# --------------------------------------------------------------------
@app.post("/vitals", response_model=schemas.VitalSignOut, status_code=201)
async def ingest_vitals(vital: schemas.VitalSignCreate, db: Session = Depends(get_db)):
    patient = db.get(models.Patient, vital.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    db_vital = models.VitalSign(**vital.model_dump())
    db.add(db_vital)
    db.commit()
    db.refresh(db_vital)

    # Run risk assessment on the new reading
    result = risk_engine.assess(
        heart_rate=db_vital.heart_rate,
        systolic_bp=db_vital.systolic_bp,
        diastolic_bp=db_vital.diastolic_bp,
        spo2=db_vital.spo2,
        temperature=db_vital.temperature,
        respiratory_rate=db_vital.respiratory_rate,
        consciousness_level=db_vital.consciousness_level,
    )
    db_assessment = models.RiskAssessment(
        patient_id=patient.id,
        vital_sign_id=db_vital.id,
        news2_score=result.news2,
        model_probability=result.probability,
        risk_level=result.risk_level,
    )
    db.add(db_assessment)
    db.commit()
    db.refresh(db_assessment)

    # Create an alert if warranted (with dedup)
    new_alert = alerts.create_alert_from_assessment(db, patient.id, db_assessment)

    # Broadcast to any connected dashboards
    await manager.broadcast({
        "type": "vital_update",
        "patient_id": patient.id,
        "risk_level": result.risk_level.value,
        "news2_score": result.news2,
        "alert_created": new_alert is not None,
    })

    return db_vital


@app.get("/patients/{patient_id}/vitals", response_model=List[schemas.VitalSignOut])
def get_patient_vitals(patient_id: int, limit: int = 100, db: Session = Depends(get_db)):
    return (
        db.query(models.VitalSign)
        .filter_by(patient_id=patient_id)
        .order_by(models.VitalSign.recorded_at.desc())
        .limit(limit)
        .all()
    )


# --------------------------------------------------------------------
# Risk assessments
# --------------------------------------------------------------------
@app.get("/patients/{patient_id}/risk-assessments", response_model=List[schemas.RiskAssessmentOut])
def get_risk_assessments(patient_id: int, limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(models.RiskAssessment)
        .filter_by(patient_id=patient_id)
        .order_by(models.RiskAssessment.assessed_at.desc())
        .limit(limit)
        .all()
    )


# --------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------
@app.get("/alerts", response_model=List[schemas.AlertOut])
def list_alerts(status: str = None, db: Session = Depends(get_db)):
    q = db.query(models.Alert)
    if status:
        q = q.filter(models.Alert.status == status)
    return q.order_by(models.Alert.created_at.desc()).all()


@app.post("/alerts/{alert_id}/acknowledge", response_model=schemas.AlertOut)
def acknowledge_alert(alert_id: int, body: schemas.AlertAcknowledge, db: Session = Depends(get_db)):
    alert = alerts.acknowledge_alert(db, alert_id, body.staff_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@app.post("/alerts/{alert_id}/resolve", response_model=schemas.AlertOut)
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = alerts.resolve_alert(db, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@app.post("/alerts/escalate-stale")
def escalate_stale(db: Session = Depends(get_db)):
    """Intended to be triggered periodically (cron / scheduler) to escalate
    alerts that have gone unacknowledged past the escalation threshold."""
    count = alerts.escalate_stale_alerts(db)
    return {"escalated": count}


# --------------------------------------------------------------------
# Staff
# --------------------------------------------------------------------
@app.post("/staff", status_code=201)
def create_staff(staff: schemas.StaffCreate, db: Session = Depends(get_db)):
    db_staff = models.Staff(**staff.model_dump())
    db.add(db_staff)
    db.commit()
    db.refresh(db_staff)
    return {"id": db_staff.id, "full_name": db_staff.full_name, "role": db_staff.role}
