#!/usr/bin/env bash
# run.sh — one-command launcher for macOS / Linux.
# Creates a venv on first run, installs deps, and starts the dashboard.
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "Installing dependencies..."
python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt -q

echo "Launching dashboard at http://localhost:8501 ..."
python3 -m streamlit run app.py
