#!/usr/bin/env bash
# Build one immutable offline candidate under dist/deploy/<candidate-id>/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env.prod}"
POSTGRES_SOURCE_IMAGE="${POSTGRES_SOURCE_IMAGE:-timescale/timescaledb:2.16.1-pg15}"
REDIS_SOURCE_IMAGE="${REDIS_SOURCE_IMAGE:-redis:7-alpine}"

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <candidate-id> <target-platform>" >&2
  echo "Example: $0 deploy-20260819.1 linux/amd64" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[export] Missing production environment file: $ENV_FILE" >&2
  exit 1
fi

CANDIDATE_ID="$1"
TARGET_PLATFORM="$2"
cd "$PROJECT_ROOT"

uv run python tools/release_artifacts.py build \
  --candidate-id "$CANDIDATE_ID" \
  --target-platform "$TARGET_PLATFORM" \
  --env-file "$ENV_FILE" \
  --output-root "$PROJECT_ROOT/dist/deploy" \
  --postgres-source "$POSTGRES_SOURCE_IMAGE" \
  --redis-source "$REDIS_SOURCE_IMAGE"
