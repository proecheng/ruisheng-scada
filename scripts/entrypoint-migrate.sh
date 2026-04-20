#!/bin/bash
set -euo pipefail

echo "[migrate] Running alembic upgrade head..."
alembic upgrade head

echo "[migrate] Running seeds..."
python tools/run_seeds.py

echo "[migrate] Database initialised successfully."
