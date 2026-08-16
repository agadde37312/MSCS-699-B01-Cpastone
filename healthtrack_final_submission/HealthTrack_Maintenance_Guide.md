# HealthTrack — Maintenance Guide

## 1. Updating or replacing components

The system has four independently replaceable pieces:

| Component | Location | How to update |
|---|---|---|
| API endpoints | `app/main.py` | Edit the FastAPI route, add/update tests in `tests/test_api.py`, run `./scripts/test.sh` |
| Risk scoring logic | `app/risk_engine.py` | Adjust NEWS2 bands or the combination rule with the RF probability; add a case to `tests/test_risk_engine.py` |
| Risk ML model | `scripts/train_risk_model.py` → `app/risk_model.joblib` | Edit training data/hyperparameters, rerun the script, restart the API (model is cached in-process and loaded on first request) |
| Dashboard | `dashboard/dash_app.py` | Edit layout/callbacks; the dashboard is a separate process, so it can be restarted without touching the API |

**General rule:** always re-run `./scripts/test.sh` (unit + integration +
UAT) after any change before deploying, since the suite exercises the
full vitals → risk assessment → alert → dashboard pipeline, not just
isolated functions.

### Retraining the risk model

```bash
source venv/bin/activate
python scripts/train_risk_model.py
```

This overwrites `app/risk_model.joblib`. Restart the API process
afterward — the model is loaded once and cached in memory
(`app/risk_engine.py`'s `_model_cache`), so a running process won't pick
up a newly trained file until restarted.

## 2. Running migrations / adding new tables

HealthTrack uses Alembic for schema migrations. The workflow below was
exercised twice while building this system: once for the initial schema,
and once to add a `patient_notes` table — both are in
`migrations/versions/` as real, working examples.

**To add a new table or column:**

1. Add or edit a model class in `app/models.py`.
2. Generate a migration from the model diff:
   ```bash
   alembic revision --autogenerate -m "describe the change"
   ```
3. **Always open the generated file in `migrations/versions/` and read it**
   before applying it. Autogenerate compares your models against the
   current database and can misdetect changes — during this project, an
   editing mistake in `app/models.py` caused autogenerate to propose
   adding two unrelated columns to the `alerts` table instead of creating
   a new table, because the mistake had merged two class bodies together.
   The generated migration was the first place this became visible, which
   is exactly why this review step matters.
4. Apply it:
   ```bash
   alembic upgrade head
   ```
5. Verify: connect to the database and confirm the new table/column
   exists, then run `./scripts/test.sh`.

**To roll back:**
```bash
alembic downgrade -1        # one step back
alembic downgrade base      # all the way back (dev/test only)
```

**Checking migration status:**
```bash
alembic current              # what's applied
alembic history               # full migration chain
```

## 3. Troubleshooting common issues

**API returns 500 on `/vitals`**
Check that `app/risk_model.joblib` exists — if missing, the risk engine
falls back to NEWS2-only scoring (this is graceful, not a crash), but a
genuinely corrupted file will raise on load. Retrain with
`python scripts/train_risk_model.py`.

**Dashboard shows "Could not reach API at ..."**
The dashboard polls the API over HTTP every 5 seconds and shows this
message when the request fails — this is by design (see
`dashboard/dash_app.py`'s `refresh()` function), not a dashboard bug.
Check that: the API process is running, `API_BASE_URL` in `.env` /
environment matches where the API is actually listening, and (Docker
deployments) both containers are on the same network.

**"MRN already exists" (409) when registering a patient**
Medical record numbers are enforced unique at the database level
(`app/models.py`, `Patient.medical_record_number`). This is expected
behavior, not a bug — search for the existing patient instead of
re-registering.

**Alerts not appearing for clearly abnormal vitals**
Check the deduplication window: `app/alerts.py`'s
`DEDUP_COOLDOWN_MINUTES` (default 15) suppresses repeat alerts for the
same patient + risk level combination. This is intentional (prevents
alert flooding), but if it seems wrong for your unit's workflow, adjust
the constant and re-test with `tests/test_alerts.py`.

**Migration fails with "table already exists"**
Usually means the database was created via `Base.metadata.create_all()`
(which `app/main.py` runs on import as a dev convenience) rather than
via Alembic. For any environment that should be tracked by migrations,
drop the dev-created tables once and let `alembic upgrade head` recreate
them, or stamp the current revision if the schema already matches:
```bash
alembic stamp head
```

**`email-validator is not installed` error**
This dependency is in `requirements.txt`; if you see this error, your
environment wasn't set up via `./scripts/setup.sh` (or a manual `pip
install -r requirements.txt` was skipped). This exact issue came up
during development — it's now pinned in requirements so a fresh install
won't reproduce it.

**`externally-managed-environment` error when installing dependencies**
Recent Debian/Ubuntu Python installations refuse global `pip install` by
default. Use the provided `./scripts/setup.sh`, which creates and
installs into a virtual environment (`venv/`) rather than the system
Python — this was hit and fixed during development of this deliverable.

## 4. Monitoring system performance

**Application-level:**
- `GET /health` — liveness check, returns current server time; wire this
  into an uptime monitor (e.g., a 30–60s interval check).
- Alert queue depth: `GET /alerts?status=Open` — a growing, unacknowledged
  queue is an operational signal (staffing, not just software).
- `POST /alerts/escalate-stale` should be triggered periodically (cron or
  a scheduler) — it is not automatic. Without this running, alerts will
  never move from Open to Escalated no matter how long they sit
  unacknowledged. A 5-minute cron interval is reasonable given the
  10-minute escalation threshold in `app/alerts.py`.

**Database-level:**
- Watch query latency on `vital_signs` and `risk_assessments` — both are
  indexed on `(patient_id, timestamp)` (see `app/models.py`) for the
  common "recent history for this patient" access pattern; if new query
  patterns emerge (e.g., unit-wide dashboards scanning across patients),
  they will likely need their own indexes.
- Table growth: `vital_signs` and `risk_assessments` grow with every
  device reading and are the fastest-growing tables. Plan retention/
  archival policy before this becomes a performance issue — this was
  called out but not implemented in this deliverable (see Known
  Limitations in the handover document).

**Model quality drift:**
- The Random Forest was trained on synthetic data (see the technical
  report for details and honest caveats about this). Once real vitals
  data is available, periodically compare the model's predicted
  probabilities against actual clinical outcomes and retrain if accuracy
  degrades — there is no automated drift detection in this build.
