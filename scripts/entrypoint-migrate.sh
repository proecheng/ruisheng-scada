#!/bin/bash
set -euo pipefail

require_secret() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" || "$value" == CHANGE_ME_* ]]; then
    echo "[migrate] $name must be set to a non-placeholder value" >&2
    exit 2
  fi
}

require_url_secret() {
  local name="$1"
  local value="${!name:-}"
  require_secret "$name"
  if [[ ! "$value" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo "[migrate] $name must use URL-safe characters: A-Z a-z 0-9 . _ ~ -" >&2
    exit 2
  fi
  if [[ ${#value} -lt 16 ]]; then
    echo "[migrate] $name must contain at least 16 characters" >&2
    exit 2
  fi
}

require_url_secret POSTGRES_PASSWORD
require_url_secret RUISHENG_GW_PASSWORD
require_url_secret RUISHENG_API_PASSWORD
require_url_secret REDIS_PASSWORD
require_secret JWT_SECRET
if [[ ${#JWT_SECRET} -lt 32 ]]; then
  echo "[migrate] JWT_SECRET must contain at least 32 characters" >&2
  exit 2
fi

echo "[migrate] Running alembic upgrade head..."
alembic upgrade head

echo "[migrate] Running seeds..."
python tools/run_seeds.py

echo "[migrate] Database initialised successfully."
