
**Author:** Arun Bhaskar Gadde | MSCS-699-B01 Capstone 

Backend implementation of the HealthTrack Remote Monitoring System: SQLAlchemy
models, a FastAPI REST API with JWT authentication and role-based access
control, an alerting subsystem and pytest tests for the alert logic.

## Project structure (this `app/` package)

Current files in this folder:

```
__init__.py
alert_crud.py
alert_models.py
alert_rules.py
alert_schemas.py
auth.py
conftest.py
crud.py
database.py
initial_schema_users_patients_vital.py
logging_config.py
main.py
models.py
notifications.py
schemas.py
seed_default_alert_config.py
test_alerts.py
```

Short descriptions:
- `main.py` — FastAPI application and route declarations (includes `/health`)
- `database.py` — SQLAlchemy engine, session factory and `Base` metadata
- `models.py`, `schemas.py` — DB models and Pydantic schemas
- `crud.py`, `alert_crud.py` — data access and alert business logic
- `auth.py` — JWT auth plus bcrypt-based password helpers
- `alert_*` modules — alert models, rules, and schemas
- `test_alerts.py`, `conftest.py` — pytest tests and fixtures for alerts

## Quick setup & run

1. Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt  # if present
# or install minimal deps:
pip install fastapi uvicorn sqlalchemy pydantic bcrypt "python-jose[cryptography]"
```

2. Run the API from the project root (one level above this `app/` folder):

```powershell
cd C:\Path\To\project-root
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Notes:
- Prefer running the package as a module (`-m`) or via `uvicorn` so imports
  resolve correctly. Running a file directly like `python app/main.py` will
  typically produce `ModuleNotFoundError: No module named 'app'`.
- The app exposes `/health` and interactive docs at `/docs` and `/redoc`.

## Tests

Run the pytest suite (tests are configured to use an in-memory SQLite DB):

```powershell
cd C:\Path\To\project-root
.venv\Scripts\python.exe -m pytest -q
```

## API endpoints of interest

- `GET /` — small friendly message (convenience)
- `GET /health` — service health
- `GET /docs` and `GET /redoc` — interactive API docs

## Notes

- The alerting subsystem (models, rules, CRUD) lives in the `alert_*` modules
  and is exercised by `test_alerts.py`.

