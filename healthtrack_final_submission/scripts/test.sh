#!/usr/bin/env bash
# HealthTrack — run unit, integration, and UAT tests.
set -e

cd "$(dirname "$0")/.."

if [ -d venv ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

echo "== Unit + integration tests (pytest) =="
pytest tests/test_risk_engine.py tests/test_alerts.py tests/test_api.py -v --cov=app --cov-report=term-missing

echo
echo "== User Acceptance Test scenarios =="
python tests/uat_scenarios.py
