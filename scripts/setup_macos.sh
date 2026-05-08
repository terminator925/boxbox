#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh and rerun this script."
  exit 1
fi

brew install ffmpeg rubberband node python@3.11

PYTHON_BIN=""
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="python3.11"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "Python 3 was not found after Homebrew installation."
  exit 1
fi

"$PYTHON_BIN" -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r backend/requirements.txt
deactivate

cd frontend
npm install

echo
echo "BoxBox macOS setup complete."
echo "Next:"
echo "  1. ./scripts/run_backend_macos.sh"
echo "  2. ./scripts/run_frontend_macos.sh"
