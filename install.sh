#!/usr/bin/env bash
# One-command install: creates .venv and installs the tool (Python 3.9+ required).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required"'
"$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"
[ -f .env ] || cp .env.example .env
echo "Installed. Start the interface with: .venv/bin/tool ui"
