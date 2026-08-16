"""
User Acceptance Testing (UAT) script for HealthTrack.

Simulates real clinical workflows end-to-end against a running instance
of the API (via TestClient, so no separate server process is needed) and
prints a pass/fail transcript for each scenario. This complements the
pytest unit/integration suite by validating full user-facing workflows
rather than individual functions.

Run: python tests/uat_scenarios.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.path.exists("./uat_healthtrack.db"):
    os.remove("./uat_healthtrack.db")
os.environ["DATABASE_URL"] = "sqlite:///./uat_healthtrack.db"

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
results = []


def scenario(name):
    def decorator(fn):
        def wrapper():
            try:
                fn()
                results.append((name, "PASS", None))
                print(f"[PASS] {name}")
            except AssertionError as e:
                results.append((name, "FAIL", str(e)))
                print(f"[FAIL] {name}: {e}")
        return wrapper
    return decorator


@scenario("UAT-1: Nurse registers a new patient")
def uat_register_patient():
    r = client.post("/patients", json={
        "medical_record_number": "MRN-UAT-001",
        "first_name": "Maria", "last_name": "Alvarez",
        "date_of_birth": "1958-03-12T00:00:00",
        "sex": "F", "primary_condition": "COPD"
    })
    assert r.status_code == 201, f"expected 201, got {r.status_code}: {r.text}"
    assert r.json()["medical_record_number"] == "MRN-UAT-001"


@scenario("UAT-2: Device streams normal vitals -> no alert raised")
def uat_normal_vitals_no_alert():
    patients = client.get("/patients").json()
    patient_id = next(p["id"] for p in patients if p["medical_record_number"] == "MRN-UAT-001")

    r = client.post("/vitals", json={
        "patient_id": patient_id, "heart_rate": 82, "systolic_bp": 128,
        "diastolic_bp": 82, "spo2": 96, "temperature": 98.6, "respiratory_rate": 18
    })
    assert r.status_code == 201

    alerts = client.get("/alerts", params={"status": "Open"}).json()
    patient_alerts = [a for a in alerts if a["patient_id"] == patient_id]
    assert len(patient_alerts) == 0, "expected no alert for normal vitals"


@scenario("UAT-3: Device streams deteriorating vitals -> critical alert raised")
def uat_critical_vitals_alert():
    patients = client.get("/patients").json()
    patient_id = next(p["id"] for p in patients if p["medical_record_number"] == "MRN-UAT-001")

    r = client.post("/vitals", json={
        "patient_id": patient_id, "heart_rate": 128, "systolic_bp": 84,
        "diastolic_bp": 52, "spo2": 89, "temperature": 101.9, "respiratory_rate": 27
    })
    assert r.status_code == 201

    alerts = client.get("/alerts", params={"status": "Open"}).json()
    patient_alerts = [a for a in alerts if a["patient_id"] == patient_id]
    assert len(patient_alerts) == 1, "expected exactly one open alert"
    assert patient_alerts[0]["severity"] in ("Critical", "Warning")


@scenario("UAT-4: Repeated critical readings do not flood the nurse with duplicate alerts")
def uat_dedup_prevents_alert_flood():
    patients = client.get("/patients").json()
    patient_id = next(p["id"] for p in patients if p["medical_record_number"] == "MRN-UAT-001")

    for _ in range(3):
        client.post("/vitals", json={
            "patient_id": patient_id, "heart_rate": 130, "systolic_bp": 83,
            "diastolic_bp": 50, "spo2": 88, "temperature": 102.1, "respiratory_rate": 28
        })

    alerts = client.get("/alerts", params={"status": "Open"}).json()
    patient_alerts = [a for a in alerts if a["patient_id"] == patient_id]
    assert len(patient_alerts) == 1, f"expected dedup to suppress repeats, found {len(patient_alerts)}"


@scenario("UAT-5: Nurse acknowledges an alert and it leaves the open queue")
def uat_nurse_acknowledges_alert():
    r = client.post("/staff", json={
        "full_name": "Nurse Alvarez", "role": "Nurse", "email": "nurse.alvarez@healthtrack.example.com"
    })
    staff_id = r.json()["id"]

    patients = client.get("/patients").json()
    patient_id = next(p["id"] for p in patients if p["medical_record_number"] == "MRN-UAT-001")
    alerts = client.get("/alerts", params={"status": "Open"}).json()
    alert_id = next(a["id"] for a in alerts if a["patient_id"] == patient_id)

    r = client.post(f"/alerts/{alert_id}/acknowledge", json={"staff_id": staff_id})
    assert r.status_code == 200
    assert r.json()["status"] == "Acknowledged"

    open_alerts = client.get("/alerts", params={"status": "Open"}).json()
    assert alert_id not in [a["id"] for a in open_alerts]


@scenario("UAT-6: Physician reviews a patient's vitals and risk history")
def uat_physician_reviews_history():
    patients = client.get("/patients").json()
    patient_id = next(p["id"] for p in patients if p["medical_record_number"] == "MRN-UAT-001")

    vitals = client.get(f"/patients/{patient_id}/vitals").json()
    assert len(vitals) >= 3, "expected multiple historical vitals readings"

    history = client.get(f"/patients/{patient_id}/risk-assessments").json()
    assert len(history) >= 3, "expected a risk assessment per vitals reading"
    assert any(h["risk_level"] == "Low" for h in history)
    assert any(h["risk_level"] in ("High", "Critical") for h in history)


@scenario("UAT-7: Duplicate patient registration (same MRN) is rejected")
def uat_duplicate_mrn_rejected():
    r = client.post("/patients", json={
        "medical_record_number": "MRN-UAT-001",  # already used above
        "first_name": "Duplicate", "last_name": "Entry",
        "date_of_birth": "1990-01-01T00:00:00"
    })
    assert r.status_code == 409


@scenario("UAT-8: Stale unacknowledged alert gets escalated")
def uat_stale_alert_escalation():
    # Register a second patient and generate a critical alert
    r = client.post("/patients", json={
        "medical_record_number": "MRN-UAT-002",
        "first_name": "Tom", "last_name": "Reed",
        "date_of_birth": "1945-06-01T00:00:00"
    })
    patient_id = r.json()["id"]
    client.post("/vitals", json={
        "patient_id": patient_id, "heart_rate": 135, "systolic_bp": 80,
        "diastolic_bp": 48, "spo2": 86, "temperature": 103.0, "respiratory_rate": 30
    })

    # backdate the alert directly via the DB to simulate time passing
    from app.database import SessionLocal
    from app.models import Alert
    db = SessionLocal()
    from datetime import datetime, timedelta
    alert = db.query(Alert).filter_by(patient_id=patient_id).first()
    alert.created_at = datetime.utcnow() - timedelta(minutes=30)
    db.commit()
    db.close()

    r = client.post("/alerts/escalate-stale")
    assert r.status_code == 200
    assert r.json()["escalated"] >= 1

    alerts = client.get("/alerts").json()
    patient_alert = next(a for a in alerts if a["patient_id"] == patient_id)
    assert patient_alert["status"] == "Escalated"


def run_all():
    uat_register_patient()
    uat_normal_vitals_no_alert()
    uat_critical_vitals_alert()
    uat_dedup_prevents_alert_flood()
    uat_nurse_acknowledges_alert()
    uat_physician_reviews_history()
    uat_duplicate_mrn_rejected()
    uat_stale_alert_escalation()

    print("\n=== UAT SUMMARY ===")
    passed = sum(1 for _, status, _ in results if status == "PASS")
    print(f"{passed}/{len(results)} scenarios passed")
    for name, status, err in results:
        line = f"{status}: {name}"
        if err:
            line += f" -- {err}"
        print(line)

    if os.path.exists("./uat_healthtrack.db"):
        os.remove("./uat_healthtrack.db")

    return all(status == "PASS" for _, status, _ in results)


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
