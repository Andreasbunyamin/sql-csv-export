#!/usr/bin/env bash
#   ./setup.sh           for PostgreSQL
#   ./setup.sh vertica   also installs the Vertica driver
set -e
cd "$(dirname "$0")"
REQ=requirements.txt
if [ "${1:-}" = "vertica" ]; then REQ=requirements-vertica.txt; fi
python3 -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r "$REQ"
[ -f .env ] || cp .env.example .env
echo "Done. Check .env, then run: .venv/bin/python main.py --test"
