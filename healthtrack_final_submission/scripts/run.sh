#!/usr/bin/env bash
# HealthTrack — run the API and dashboard together (local/dev use).
# For production, run each service under a process manager (systemd,
# supervisor, or the Docker Compose setup in deployment/) instead.
set -e

cd "$(dirname "$0")/.."

if [ -d venv ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "Starting FastAPI backend on http://${API_HOST:-0.0.0.0}:${API_PORT:-8000} ..."
uvicorn app.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" &
API_PID=$!

sleep 2

echo "Starting Dash dashboard on http://${DASHBOARD_HOST:-0.0.0.0}:${DASHBOARD_PORT:-8050} ..."
python dashboard/dash_app.py &
DASH_PID=$!

trap "echo 'Shutting down...'; kill $API_PID $DASH_PID 2>/dev/null" INT TERM

wait $API_PID $DASH_PID
