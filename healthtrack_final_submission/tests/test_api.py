import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_health_check(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_and_get_patient(client):
    r = client.post("/patients", json={
        "medical_record_number": "MRN-API-1",
        "first_name": "Alex", "last_name": "Kim",
        "date_of_birth": "1985-05-01T00:00:00",
        "sex": "M", "primary_condition": "Diabetes"
    })
    assert r.status_code == 201
    patient_id = r.json()["id"]

    r = client.get(f"/patients/{patient_id}")
    assert r.status_code == 200
    assert r.json()["medical_record_number"] == "MRN-API-1"


def test_duplicate_mrn_rejected(client):
    payload = {
        "medical_record_number": "MRN-DUP",
        "first_name": "A", "last_name": "B",
        "date_of_birth": "1985-05-01T00:00:00"
    }
    r1 = client.post("/patients", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/patients", json=payload)
    assert r2.status_code == 409


def test_get_nonexistent_patient_404(client):
    r = client.get("/patients/9999")
    assert r.status_code == 404


def test_vitals_ingestion_triggers_risk_assessment_and_alert(client):
    r = client.post("/patients", json={
        "medical_record_number": "MRN-API-2",
        "first_name": "Sam", "last_name": "Lee",
        "date_of_birth": "1970-01-01T00:00:00"
    })
    patient_id = r.json()["id"]

    r = client.post("/vitals", json={
        "patient_id": patient_id, "heart_rate": 135, "systolic_bp": 82,
        "diastolic_bp": 50, "spo2": 87, "temperature": 102.8,
        "respiratory_rate": 30, "consciousness_level": "Alert"
    })
    assert r.status_code == 201

    r = client.get(f"/patients/{patient_id}/risk-assessments")
    assert r.status_code == 200
    assessments = r.json()
    assert len(assessments) == 1
    assert assessments[0]["risk_level"] == "Critical"

    r = client.get("/alerts", params={"status": "Open"})
    assert r.status_code == 200
    alerts = [a for a in r.json() if a["patient_id"] == patient_id]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "Critical"


def test_vitals_for_nonexistent_patient_404(client):
    r = client.post("/vitals", json={"patient_id": 99999, "heart_rate": 80})
    assert r.status_code == 404


def test_normal_vitals_do_not_create_alert(client):
    r = client.post("/patients", json={
        "medical_record_number": "MRN-API-3",
        "first_name": "Robin", "last_name": "Fox",
        "date_of_birth": "1995-01-01T00:00:00"
    })
    patient_id = r.json()["id"]

    r = client.post("/vitals", json={
        "patient_id": patient_id, "heart_rate": 75, "systolic_bp": 118,
        "diastolic_bp": 76, "spo2": 98, "temperature": 98.3, "respiratory_rate": 15
    })
    assert r.status_code == 201

    r = client.get("/alerts", params={"status": "Open"})
    alerts = [a for a in r.json() if a["patient_id"] == patient_id]
    assert len(alerts) == 0


def test_alert_acknowledge_and_resolve_flow(client):
    r = client.post("/staff", json={
        "full_name": "Nurse Joy", "role": "Nurse", "email": "joy@healthtrack.example.com"
    })
    staff_id = r.json()["id"]

    r = client.post("/patients", json={
        "medical_record_number": "MRN-API-4",
        "first_name": "Pat", "last_name": "Doe",
        "date_of_birth": "1960-01-01T00:00:00"
    })
    patient_id = r.json()["id"]

    r = client.post("/vitals", json={
        "patient_id": patient_id, "heart_rate": 135, "systolic_bp": 82,
        "diastolic_bp": 50, "spo2": 87, "temperature": 102.8, "respiratory_rate": 30
    })
    alert_id = None
    r = client.get("/alerts", params={"status": "Open"})
    for a in r.json():
        if a["patient_id"] == patient_id:
            alert_id = a["id"]
    assert alert_id is not None

    r = client.post(f"/alerts/{alert_id}/acknowledge", json={"staff_id": staff_id})
    assert r.status_code == 200
    assert r.json()["status"] == "Acknowledged"

    r = client.post(f"/alerts/{alert_id}/resolve")
    assert r.status_code == 200
    assert r.json()["status"] == "Resolved"


def test_acknowledge_nonexistent_alert_404(client):
    r = client.post("/alerts/99999/acknowledge", json={"staff_id": 1})
    assert r.status_code == 404


def test_websocket_receives_broadcast_on_vital_ingestion(client):
    r = client.post("/patients", json={
        "medical_record_number": "MRN-WS-1",
        "first_name": "Wes", "last_name": "Socket",
        "date_of_birth": "1980-01-01T00:00:00"
    })
    patient_id = r.json()["id"]

    with client.websocket_connect("/ws/alerts") as websocket:
        r = client.post("/vitals", json={
            "patient_id": patient_id, "heart_rate": 135, "systolic_bp": 82,
            "diastolic_bp": 50, "spo2": 87, "temperature": 102.8, "respiratory_rate": 30
        })
        assert r.status_code == 201

        message = websocket.receive_json()
        assert message["type"] == "vital_update"
        assert message["patient_id"] == patient_id
        assert message["risk_level"] == "Critical"
        assert message["alert_created"] is True
