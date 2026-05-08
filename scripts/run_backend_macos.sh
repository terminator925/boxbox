#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x backend/.venv/bin/python ]]; then
  echo "Missing backend virtual environment. Run ./scripts/setup_macos.sh first."
  exit 1
fi

export BOXBOX_INFER_ACCELERATOR="${BOXBOX_INFER_ACCELERATOR:-torch}"
export BOXBOX_INFER_CANDIDATE_STRATEGY="${BOXBOX_INFER_CANDIDATE_STRATEGY:-core4_adaptive_plus}"
export BOXBOX_HYBRID_SEARCH_STRATEGY="${BOXBOX_HYBRID_SEARCH_STRATEGY:-core4}"

./backend/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
