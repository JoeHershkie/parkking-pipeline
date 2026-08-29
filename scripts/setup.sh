#!/usr/bin/env bash
# One-time / after-pull setup: editable install of pipeline + dev tools.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv"

if [[ ! -d "$VENV" ]]; then
  echo "Creating virtualenv at .venv ..."
  python3 -m venv "$VENV"
fi

echo "Installing parking-pipeline (editable) ..."
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV/bin/python" -m pip install -e "${ROOT}[dev]"

echo ""
echo "Done. Activate the environment:"
echo "  source .venv/bin/activate"
echo ""
echo "Console scripts: parking-clean, parking-parse-schedule, parking-parse-between,"
echo "  parking-resolve, parking-geo, parking-run"
