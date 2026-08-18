#!/bin/bash
# 在本地开发机上运行，构建并导出镜像到 deploy/images/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGES_DIR="$SCRIPT_DIR/images"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env.prod}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[export] Missing production environment file: $ENV_FILE" >&2
  exit 1
fi

echo "[export] Building images..."
cd "$PROJECT_ROOT"
docker compose -f docker-compose.prod.yml --env-file "$ENV_FILE" build --quiet

echo "[export] Pulling runtime images..."
docker compose -f docker-compose.prod.yml --env-file "$ENV_FILE" pull postgres redis

echo "[export] Creating images directory..."
mkdir -p "$IMAGES_DIR"

echo "[export] Exporting images referenced by Compose..."
declare -A SEEN_ARCHIVES=()
mapfile -t COMPOSE_IMAGES < <(
  docker compose -f docker-compose.prod.yml --env-file "$ENV_FILE" config --images \
    | awk 'NF && !seen[$0]++'
)
if [[ ${#COMPOSE_IMAGES[@]} -eq 0 ]]; then
  echo "[export] Compose did not resolve any images" >&2
  exit 1
fi
for image in "${COMPOSE_IMAGES[@]}"; do
  archive_name="$(printf '%s' "$image" | tr '/:' '__').tar.gz"
  if [[ -n "${SEEN_ARCHIVES[$archive_name]:-}" ]]; then
    echo "[export] Archive name collision for $image: $archive_name" >&2
    exit 1
  fi
  SEEN_ARCHIVES[$archive_name]=1
  echo "[export]   $image -> $archive_name"
  docker save "$image" | gzip > "$IMAGES_DIR/$archive_name"
done

echo "[export] Done. Files in $IMAGES_DIR:"
ls -lh "$IMAGES_DIR/"
echo ""
echo "Transfer the 'deploy/' folder to the customer machine."
