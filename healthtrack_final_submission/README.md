# HealthTrack Patient Monitoring System — Final Project Submission

**Author:** Arun Bhaskar Gadde
**Course:** MSCS-699 — Capstone Project, University of the Cumberlands

This is the final, integrated submission for the HealthTrack capstone: a
working patient monitoring system with vitals ingestion, combined
rule-based (NEWS2) and machine-learning (Random Forest) risk assessment,
deduplicated and escalating alerting, and a real-time dashboard.

## Start here

- **Final Technical Report:** [`docs/Final_Technical_Report.pdf`](docs/Final_Technical_Report.pdf)
  — system architecture, database design, risk methodology, alert
  design, testing summary, known limitations, and design decisions.
- **Deployment Guide:** [`docs/deployment_guide.md`](docs/deployment_guide.md)
  — step-by-step instructions for local and Docker deployment.
- **Maintenance Guide:** [`docs/maintenance_guide.md`](docs/maintenance_guide.md)
  — updating components, running migrations, troubleshooting, monitoring.
- **Handover Documentation:** [`docs/handover_documentation.md`](docs/handover_documentation.md)
  — project overview, mock admin credentials, known limitations, next
  steps, authorship.
- **Test Results and Analysis:** [`docs/test_results.md`](docs/test_results.md)
  — full pytest/UAT output and an honest read of the risk model's
  performance.

## Quick start

```bash
./scripts/setup.sh   # creates venv, installs deps, runs migrations, trains risk model
./scripts/run.sh      # starts the API (port 8000) and dashboard (port 8050)
./scripts/test.sh     # runs unit, integration, and UAT test suites
```

Then open `http://localhost:8050` for the dashboard, or
`http://localhost:8000/docs` for interactive API documentation.

## Repository layout

```
app/                    FastAPI backend, SQLAlchemy models, risk engine, alert manager
dashboard/               Dash real-time dashboard
migrations/              Alembic database migrations
scripts/                 setup.sh, run.sh, test.sh, train_risk_model.py
tests/                   pytest unit/integration tests + UAT scenario script
deployment/               Dockerfile.api, Dockerfile.dashboard, docker-compose.yml, .env.example
docs/                     Final report (PDF), deployment/maintenance/handover guides, test results
requirements.txt          Pinned Python dependencies
alembic.ini               Alembic configuration
```

## Current status (see the Final Technical Report for full detail)

- 26/26 unit and integration tests passing, 97% code coverage on `app/`
- 8/8 User Acceptance Test scenarios passing
- Local/single-server deployment verified end-to-end from a clean environment
- Docker Compose deployment written and YAML-validated but **not**
  verified (Docker unavailable in the build environment) — flagged
  explicitly rather than presented as tested
- No authentication layer yet — **not** production-ready for real patient
  data as-is; see Known Limitations in the Final Technical Report and
  Handover Documentation for the full, honest list
