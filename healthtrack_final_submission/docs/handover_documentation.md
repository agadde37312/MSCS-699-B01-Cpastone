# HealthTrack — Handover Documentation

## 1. Project overview

HealthTrack is a patient monitoring system built across a series of MSCS
capstone deliverables. It ingests patient vital signs, runs a combined
rule-based (NEWS2) and machine-learning (Random Forest) risk assessment
on each reading, raises deduplicated and escalating alerts for at-risk
patients, and displays open alerts on a real-time dashboard for clinical
staff.

**Core components:**
- FastAPI backend (`app/`) — patient, vitals, risk assessment, alert, and
  staff endpoints, plus a WebSocket channel for real-time push updates
- SQLAlchemy models + Alembic migrations (`app/models.py`,
  `migrations/`) — 7 tables covering the working subset of the full
  20-entity schema designed in the earlier database design phase
- Risk engine (`app/risk_engine.py`) — NEWS2 scoring combined with a
  trained Random Forest classifier
- Alert manager (`app/alerts.py`) — deduplication and escalation logic
- Dash dashboard (`dashboard/dash_app.py`) — real-time open-alerts view

**Status at handover:** all automated tests pass (26/26 unit and
integration tests, 8/8 UAT scenarios, 96%+ code coverage on `app/`). The
local/single-server deployment path has been run end-to-end and
verified. See the Final Technical Report and Test Results documents for
details.

## 2. Admin credentials (mock data)

No authentication layer exists in this build (see Known Limitations
below), so there are no real admin credentials to hand over. The
"credentials" below are mock records seeded via the API for
demonstration and testing purposes only — they are database rows in the
`staff` table, not login accounts.

| Field | Mock value |
|---|---|
| Full name | Nurse Alvarez |
| Role | Nurse |
| Email | nurse.alvarez@healthtrack.example.com |
| Staff ID | Assigned by the database on creation (auto-increment) |

To create additional mock staff records:
```bash
curl -X POST http://localhost:8000/staff \
  -H "Content-Type: application/json" \
  -d '{"full_name": "Dr. Kim", "role": "Physician", "email": "dr.kim@healthtrack.example.com"}'
```

**Before any real deployment**, an authentication/authorization layer
(e.g., OAuth2 + JWT via FastAPI's built-in security utilities, or an
identity provider integration) must be added — this system currently
has no login, no access control, and no audit trail of who viewed what.
This is the single most important gap to close before handling real
patient data; see Known Limitations.

## 3. Known limitations

Documented honestly, in priority order for whoever picks this up next:

1. **No authentication or authorization.** Every endpoint is open. This
   is acceptable for a coursework/demo build but is disqualifying for
   any real clinical use — real patient data requires access control and
   an audit trail (who viewed/changed what, when) at minimum for HIPAA-
   adjacent compliance, regardless of jurisdiction specifics.
2. **Risk model trained on synthetic data.** The Random Forest in
   `app/risk_model.joblib` was trained on a synthetically generated
   vitals dataset (see `scripts/train_risk_model.py`), not real patient
   outcomes. Its ~0.63 F1 on the "High risk" class is a reasonable
   result for a model with this training data, but it has not been
   validated against real clinical outcomes and should not be trusted
   for real triage decisions without that validation.
3. **No automated alert escalation trigger.** `POST
   /alerts/escalate-stale` exists and works (see Maintenance Guide) but
   nothing calls it automatically — a cron job or scheduler needs to be
   added in any real deployment, or stale alerts will sit un-escalated
   indefinitely.
4. **Naive (not timezone-aware) datetimes throughout.** This was a
   deliberate choice made during development (see Final Technical
   Report, Design Decisions) to avoid a subtler bug from mixing aware
   and naive datetimes with SQLite, but it means the system implicitly
   assumes a single server timezone (UTC) and hasn't been tested across
   multi-region deployments.
5. **No data retention/archival policy.** `vital_signs` and
   `risk_assessments` grow unbounded with every reading. Fine for a
   demo; needs a retention policy before production scale.
6. **Docker Compose path unverified.** Written and YAML-validated but not
   actually run (Docker wasn't available in the build environment) — see
   Deployment Guide, Section 3.
7. **Dashboard has one view.** It currently shows only the open-alerts
   queue; a real deployment would likely want per-patient trend views,
   filtering by unit/ward, and role-based views (nurse vs. physician vs.
   admin).
8. **No notification channel beyond the dashboard.** Alerts are visible
   only to someone watching the dashboard — there's no SMS/pager/email
   integration for off-screen staff.

## 4. Next-step recommendations

In rough priority order:

1. Add authentication (FastAPI + OAuth2/JWT is the path of least
   resistance given the existing stack) before any real data touches
   this system.
2. Stand up a scheduler (APScheduler, Celery beat, or a simple cron
   entry) to call `POST /alerts/escalate-stale` on a fixed interval.
3. Replace the synthetic training data with real (de-identified)
   historical vitals + outcomes, if/when available, and re-validate the
   risk model's performance before trusting its output clinically.
4. Add role-based dashboard views and a notification channel for
   off-screen staff (the two dashboard-related limitations above).
5. Actually run the Docker Compose path in a real Docker environment and
   fix whatever surfaces — it was written carefully but is unverified.
6. Define and implement a data retention policy for `vital_signs` and
   `risk_assessments` before this runs at real scale.

## 5. Contact / authorship

**Author:** Arun Bhaskar Gadde
**Course:** MSCS-699 — Capstone Project, University of the Cumberlands
**Project:** HealthTrack Patient Monitoring System
**Deliverable:** Final Project Submission

This document, along with the Final Technical Report, Deployment Guide,
Maintenance Guide, and Test Results, represents the complete handover
package for this deliverable.
