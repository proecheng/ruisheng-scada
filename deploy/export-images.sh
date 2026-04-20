#!/bin/bash
# 在本地开发机上运行，构建并导出镜像到 deploy/images/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGES_DIR="$SCRIPT_DIR/images"

echo "[export] Building images..."
cd "$PROJECT_ROOT"
docker compose -f docker-compose.prod.yml --env-file .env.prod build --quiet

echo "[export] Creating images directory..."
mkdir -p "$IMAGES_DIR"

echo "[export] Exporting application images..."
docker save ruisheng-prod-api:latest | gzip > "$IMAGES_DIR/ruisheng-api.tar.gz"
docker save ruisheng-prod-gw:latest  | gzip > "$IMAGES_DIR/ruisheng-gw.tar.gz"
docker save ruisheng-prod-web:latest | gzip > "$IMAGES_DIR/ruisheng-web.tar.gz"

echo "[export] Exporting base images..."
docker save timescale/timescaledb:2.16.1-pg15 | gzip > "$IMAGES_DIR/timescaledb.tar.gz"
docker save redis:7-alpine | gzip > "$IMAGES_DIR/redis.tar.gz"

echo "[export] Done. Files in $IMAGES_DIR:"
ls -lh "$IMAGES_DIR/"
echo ""
echo "Transfer the 'deploy/' folder to the customer machine."
