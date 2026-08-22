#!/bin/bash
# Build one immutable offline candidate under dist/deploy/<candidate-id>/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env.prod}"
RELEASE_OUTPUT_ROOT="${RELEASE_OUTPUT_ROOT:-}"
POSTGRES_SOURCE_IMAGE="${POSTGRES_SOURCE_IMAGE:-timescale/timescaledb:2.16.1-pg15}"
REDIS_SOURCE_IMAGE="${REDIS_SOURCE_IMAGE:-redis:7-alpine}"

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <candidate-id> <target-platform> <agent-backed-public-identity.pub> <trust-directory>" >&2
  echo "The encrypted private key must already be unlocked in ssh-agent; never pass it here." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[export] Missing production environment file: $ENV_FILE" >&2
  exit 1
fi
if [[ -z "$RELEASE_OUTPUT_ROOT" ]]; then
  echo "[export] RELEASE_OUTPUT_ROOT must name an administrator-protected publish directory." >&2
  exit 1
fi

CANDIDATE_ID="$1"
TARGET_PLATFORM="$2"
SIGNING_IDENTITY="$3"
TRUST_DIRECTORY="$4"
cd "$PROJECT_ROOT"

uv run python tools/release_artifacts.py build \
  --candidate-id "$CANDIDATE_ID" \
  --target-platform "$TARGET_PLATFORM" \
  --env-file "$ENV_FILE" \
  --output-root "$RELEASE_OUTPUT_ROOT" \
  --signing-identity "$SIGNING_IDENTITY" \
  --trust-directory "$TRUST_DIRECTORY" \
  --postgres-source "$POSTGRES_SOURCE_IMAGE" \
  --redis-source "$REDIS_SOURCE_IMAGE"
