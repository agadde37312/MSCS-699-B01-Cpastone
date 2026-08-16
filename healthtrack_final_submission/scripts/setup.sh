#!/usr/bin/env bash
# HealthTrack — first-time setup
# Creates a virtual environment, installs dependencies, runs database
# migrations, and trains the risk model.
set -e

cd "$(dirname "$0")/.."

if [ ! -d venv ]; then
    echo "== Creating virtual environment (venv/) =="
    python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "== Installing Python dependencies =="
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
    echo "== Creating .env from template =="
    cp deployment/.env.example .env
    echo "Edit .env to set DATABASE_URL for your environment before running in production."
fi

echo "== Running database migrations =="
alembic upgrade head

echo "== Training risk assessment model =="
python scripts/train_risk_model.py

echo "== Setup complete. Next: ./scripts/run.sh =="
