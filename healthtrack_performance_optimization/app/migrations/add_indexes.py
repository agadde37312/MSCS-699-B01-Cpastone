"""
Adds indexes identified as missing during performance analysis:

1. vitals_readings(patient_id, timestamp DESC) -- composite index covering the
   most common query pattern in the app: "get this patient's readings ordered
   by time." Without this, SQLite does a full table SCAN plus a temp B-tree
   sort for every call (confirmed via EXPLAIN QUERY PLAN before this migration).

2. risk_reports(patient_id, generated_at) -- same reasoning for risk-history
   lookups.

Run with: python3 -m app.migrations.add_indexes
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.database import engine


STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS ix_vitals_readings_patient_timestamp "
    "ON vitals_readings (patient_id, timestamp DESC)",

    "CREATE INDEX IF NOT EXISTS ix_risk_reports_patient_generated "
    "ON risk_reports (patient_id, generated_at DESC)",
]


def upgrade():
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            print(f"Executing: {stmt}")
            conn.execute(text(stmt))
    print("Indexes created.")


if __name__ == "__main__":
    upgrade()
