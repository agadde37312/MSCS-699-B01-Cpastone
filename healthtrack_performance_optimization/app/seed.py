"""
Seeds the performance-testbed database with a realistic data volume:
600 patients x ~50 readings each (~30,000 readings total), spread across a
few weeks, so that query performance differences are actually measurable
instead of hidden by a tiny dataset.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Patient, VitalsReading

DB_PATH = os.path.join(os.path.dirname(__file__), "healthtrack_perf.db")


def seed(n_patients=2000, readings_per_patient=80, seed_value=42, db_path=DB_PATH):
    if os.path.exists(db_path):
        os.remove(db_path)

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    rng = random.Random(seed_value)
    start_date = datetime(2026, 1, 1)

    patients = []
    for i in range(1, n_patients + 1):
        p = Patient(
            id=i,
            name=f"Patient {i:04d}",
            age=rng.randint(22, 90),
            diabetes=rng.random() < 0.28,
            hypertension=rng.random() < 0.38,
            copd=rng.random() < 0.12,
            heart_disease=rng.random() < 0.20,
            created_at=start_date,
        )
        patients.append(p)
    session.bulk_save_objects(patients)
    session.commit()

    reading_id = 1
    all_readings = []
    for pid in range(1, n_patients + 1):
        base_hr = rng.normalvariate(75, 8)
        base_sbp = rng.normalvariate(122, 12)
        base_spo2 = rng.normalvariate(97, 1.2)
        base_temp = rng.normalvariate(36.8, 0.3)
        base_rr = rng.normalvariate(16, 2)
        base_glucose = rng.normalvariate(100, 15)

        # Simulate a small number of "heavy" long-term monitoring patients
        # with far more readings than average -- realistic for remote
        # monitoring programs where some patients are enrolled much longer.
        n_readings = readings_per_patient
        if pid <= 20:
            n_readings = readings_per_patient * 15

        ts = start_date
        for r in range(n_readings):
            ts = ts + timedelta(hours=rng.uniform(4, 8))
            all_readings.append(VitalsReading(
                id=reading_id,
                patient_id=pid,
                timestamp=ts,
                heart_rate=max(35, base_hr + rng.normalvariate(0, 6)),
                systolic_bp=max(70, base_sbp + rng.normalvariate(0, 8)),
                diastolic_bp=max(40, base_sbp * 0.65 + rng.normalvariate(0, 6)),
                spo2=min(100, max(80, base_spo2 + rng.normalvariate(0, 1.5))),
                temperature_c=base_temp + rng.normalvariate(0, 0.3),
                respiratory_rate=max(8, base_rr + rng.normalvariate(0, 2)),
                blood_glucose=max(50, base_glucose + rng.normalvariate(0, 12)),
            ))
            reading_id += 1

        if len(all_readings) >= 5000:
            session.bulk_save_objects(all_readings)
            session.commit()
            all_readings = []

    if all_readings:
        session.bulk_save_objects(all_readings)
        session.commit()

    n_patients_check = session.query(Patient).count()
    n_readings_check = session.query(VitalsReading).count()
    session.close()

    print(f"Seeded {n_patients_check} patients and {n_readings_check} readings into {db_path}")
    return db_path


if __name__ == "__main__":
    seed()
