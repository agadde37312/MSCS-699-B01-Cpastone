# HealthTrack — Test Results and Analysis

All results below are from a single, fresh run of the full test suite
against the final codebase (commit state at handover), captured
immediately before this document was written — not recalled or
estimated from earlier runs.

## 1. Unit and integration tests (pytest)

**Command:** `pytest tests/test_risk_engine.py tests/test_alerts.py tests/test_api.py -v --cov=app --cov-report=term-missing`

**Result: 26 passed, 0 failed**

| Test file | Tests | Focus |
|---|---|---|
| `test_risk_engine.py` | 9 | NEWS2 scoring bands, missing-value handling, Random Forest probability loading, combined risk-level logic |
| `test_alerts.py` | 7 | Alert creation, deduplication within cooldown, cooldown expiry, acknowledge/resolve state transitions, stale-alert escalation |
| `test_api.py` | 10 | Patient CRUD, duplicate MRN rejection, 404 handling, vitals ingestion → risk assessment → alert pipeline, alert acknowledge/resolve flow, WebSocket broadcast on vitals ingestion |

### Code coverage

```
Name                 Stmts   Miss  Cover   Missing
--------------------------------------------------
app/__init__.py          0      0   100%
app/alerts.py           50      1    98%   92
app/database.py         12      0   100%
app/main.py            111      8    93%   41-42, 44, 87, 150, 196, 204-205
app/models.py          109      0   100%
app/risk_engine.py      59      5    92%   36, 95, 106, 134, 136
app/schemas.py          67      0   100%
--------------------------------------------------
TOTAL                  408     14    97%
```

**Uncovered lines, and why they're acceptable to leave uncovered:**
- `app/main.py` lines 41-42, 44: the WebSocket disconnect exception path
  in `ConnectionManager.broadcast()` — hit only when a client disconnects
  mid-broadcast, which the WebSocket test doesn't simulate.
- `app/main.py` line 87, 150, 196, 204-205: minor branches in staff
  creation and a couple of error-response paths not exercised by the
  current UAT scenario set.
- `app/risk_engine.py` line 36: the `else: return 0` fallback in
  `_score_band` when a value matches no band — in practice every band
  list covers the full range, so this is unreachable by design and kept
  as a defensive fallback.
- `app/risk_engine.py` lines 95, 106, 134, 136: the "no trained model"
  fallback path (`model is None`) — not hit in this run because the
  model file was present, as it should be in any deployed environment.

None of the uncovered lines are on the primary vitals → risk → alert
path; they're edge cases and defensive fallbacks.

## 2. User Acceptance Testing (UAT)

**Command:** `python tests/uat_scenarios.py`

**Result: 8/8 scenarios passed**

| Scenario | Simulates | Result |
|---|---|---|
| UAT-1 | Nurse registers a new patient | PASS |
| UAT-2 | Device streams normal vitals → no alert raised | PASS |
| UAT-3 | Device streams deteriorating vitals → critical alert raised | PASS |
| UAT-4 | Repeated critical readings do not flood the nurse with duplicate alerts | PASS |
| UAT-5 | Nurse acknowledges an alert and it leaves the open queue | PASS |
| UAT-6 | Physician reviews a patient's vitals and risk history | PASS |
| UAT-7 | Duplicate patient registration (same MRN) is rejected | PASS |
| UAT-8 | Stale unacknowledged alert gets escalated | PASS |

These scenarios exercise the system the way a nurse or physician
actually would — through the same API a real client would call, not
through direct database or function access — so a pass here means the
user-facing workflow works, not just the underlying code.

## 3. Final system / integration testing

Beyond the automated suites above, the following were manually verified
against a **live, running instance** (not TestClient mocks) during this
deliverable:

- Started the FastAPI server and Dash dashboard as separate real
  processes, communicating over actual HTTP (not in-process test
  clients).
- Posted critical vitals via `curl` to the running API; confirmed a risk
  assessment and alert were created in the database.
- Called the dashboard's `refresh()` function against the live API and
  confirmed it correctly displayed the alert (severity, message, count)
  pulled over real HTTP.
- Confirmed the dashboard degrades gracefully (clear "Could not reach
  API" message, no crash) when the API is unreachable.
- Ran a from-scratch deployment in a clean directory: fresh virtual
  environment, `./scripts/setup.sh`, `./scripts/run.sh` — both services
  came up and passed `/health` and dashboard HTTP checks.
- Ran the Alembic migration chain forward and backward twice (initial
  schema, then the `patient_notes` addition), confirming the app
  functions correctly against a migration-built schema, not just one
  created via `Base.metadata.create_all()`.

## 4. Risk model performance (separate from the app test suite)

The Random Forest model bundled at `app/risk_model.joblib` was trained
by `scripts/train_risk_model.py` on a synthetic 2,000-patient vitals
dataset (80/20 train/test split). Its held-out test performance from the
most recent training run:

```
Classes: ['High' 'Low']
Test accuracy: 0.88
Test F1 (High): 0.625

              precision    recall  f1-score   support
        High       0.74      0.54      0.62        74
         Low       0.90      0.96      0.93       326
    accuracy                           0.88       400
```

**Honest interpretation:** overall accuracy (88%) looks strong but is
inflated by class imbalance (only ~19% of patients are "High" risk in
the synthetic data, matching a low base rate you'd expect in a general
ward). The number that actually matters for a monitoring system is
recall on the High-risk class — currently 0.54, meaning the model alone
misses roughly 46% of true high-risk cases on this synthetic test set.
This is exactly why the risk engine does not rely on the model alone: it
combines the RF probability with the rule-based NEWS2 score (see the
Final Technical Report's Risk Assessment Methodology section), so a
patient with a high NEWS2 score still gets flagged even when the model's
probability estimate is uncertain. This combination is a design decision
to compensate for the model's imperfect recall, not a claim that the
combined system is clinically validated — see Known Limitations in the
handover document.

## 5. Known gaps in test coverage

In the interest of an honest account rather than a polished one:

- No load/performance testing was done (concurrent vitals ingestion
  rate, dashboard refresh under many simultaneous connections).
- No test exercises the Docker Compose deployment path, since Docker was
  unavailable in the build environment (see Deployment Guide).
- The risk model's performance is validated only against its own
  synthetic held-out test set, not against independent or real clinical
  data.
- No security testing (the system currently has no authentication to
  test against — see Known Limitations in the handover document).
