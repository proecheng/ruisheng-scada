# Local + Customer Deployment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the SCADA monorepo into Docker Compose so it boots end-to-end on the local machine, then export portable image tarballs for deployment on the customer's machine.

**Architecture:** Six Docker services — postgres (TimescaleDB), redis, migrate (one-shot init), api (FastAPI/uvicorn), gw (Modbus gateway), web (nginx serving Vue build + proxying /api and /ws). The migrate service runs alembic migrations + seeds before api/gw start. nginx is the only internet-facing service (port 80); the API is internal-only. The GW exposes port 5020 for Modbus TCP devices.

**Tech Stack:** Docker Compose v2, Python 3.11, uv workspace, nginx:alpine, node:20-alpine, pnpm

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `ruisheng-api/Dockerfile` | Create | Python 3.11 + uv; installs ruisheng-api + alembic + seeds runner |
| `ruisheng-gw/Dockerfile` | Create | Python 3.11 + uv; installs ruisheng-gw[serial] |
| `ruisheng-web/Dockerfile` | Create | Multi-stage: pnpm build → nginx:alpine serve |
| `ruisheng-web/nginx.conf` | Create | Serve SPA; proxy /api/ and /ws to api:8000 |
| `scripts/entrypoint-migrate.sh` | Create | Runs alembic upgrade head, then seeds |
| `docker-compose.prod.yml` | Create | All 6 services wired together |
| `.env.prod.example` | Create | All required env vars with comments |
| `seeds/01_users.sql` | Modify | Replace placeholder bcrypt hash with real hash |
| `deploy/setup-customer.md` | Create | Customer setup guide (image load + run) |

---

## Task 1: ruisheng-api Dockerfile

**Files:**
- Create: `ruisheng-api/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy workspace manifests first — layer cache: only invalidates on lock change
COPY pyproject.toml uv.lock ./

# Copy workspace members needed by api
COPY ruisheng-shared/ ruisheng-shared/
COPY ruisheng-api/ ruisheng-api/

# Copy alembic + seeds + migration entrypoint (used by migrate service)
COPY alembic/ alembic/
COPY alembic.ini ./
COPY seeds/ seeds/
COPY tools/ tools/
COPY scripts/ scripts/

# Install api package and its workspace deps (ruisheng-shared), no dev extras
RUN uv sync --package ruisheng-api --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["python", "-m", "ruisheng_api"]
```

- [ ] **Step 2: Verify build**

```bash
cd D:/江苏润盛
docker build -f ruisheng-api/Dockerfile -t ruisheng-api:local .
```

Expected: `=> exporting to image` with no errors. Image ~400 MB.

- [ ] **Step 3: Commit**

```bash
git add ruisheng-api/Dockerfile
git commit -m "chore(deploy): add ruisheng-api Dockerfile"
```

---

## Task 2: ruisheng-gw Dockerfile

**Files:**
- Create: `ruisheng-gw/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.11-slim

# pyserial-asyncio optional dep needs gcc for wheel build on slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY ruisheng-shared/ ruisheng-shared/
COPY ruisheng-gw/ ruisheng-gw/

# Install gw with optional serial support
RUN uv sync --package ruisheng-gw --no-dev --frozen --extra serial

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 5020 9090
CMD ["python", "-m", "ruisheng_gw"]
```

- [ ] **Step 2: Verify build**

```bash
docker build -f ruisheng-gw/Dockerfile -t ruisheng-gw:local .
```

Expected: Build succeeds, `pyserial-asyncio` installed.

- [ ] **Step 3: Commit**

```bash
git add ruisheng-gw/Dockerfile
git commit -m "chore(deploy): add ruisheng-gw Dockerfile"
```

---

## Task 3: ruisheng-web Dockerfile + nginx.conf

**Files:**
- Create: `ruisheng-web/Dockerfile`
- Create: `ruisheng-web/nginx.conf`

- [ ] **Step 1: Write nginx.conf**

```nginx
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # Vue SPA — all non-asset paths fall back to index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # REST API proxy
    location /api/ {
        proxy_pass http://api:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
    }

    # WebSocket proxy
    location /ws {
        proxy_pass http://api:8000/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    # Gzip for assets
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
}
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# ---- Build stage ----
FROM node:20-alpine AS builder

RUN npm install -g pnpm@9

WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

# Production Vite env vars — nginx serves /api and /ws prefixes
ENV VITE_API_BASE=/api
ENV VITE_WS_BASE=/ws

RUN pnpm build

# ---- Serve stage ----
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
```

- [ ] **Step 3: Verify build**

```bash
# Build context is ruisheng-web directory
docker build -f ruisheng-web/Dockerfile -t ruisheng-web:local ruisheng-web/
```

Expected: Two stages complete, final image ~30 MB.

- [ ] **Step 4: Commit**

```bash
git add ruisheng-web/Dockerfile ruisheng-web/nginx.conf
git commit -m "chore(deploy): add ruisheng-web Dockerfile and nginx.conf"
```

---

## Task 4: Migration entrypoint script + seed hash fix

**Files:**
- Create: `scripts/entrypoint-migrate.sh`
- Modify: `seeds/01_users.sql`

- [ ] **Step 1: Generate a real bcrypt hash for the demo admin user**

The seeds currently have `'$2b$12$PLACEHOLDER_BCRYPT_HASH'` which will not allow login. Generate a real hash:

```bash
docker run --rm python:3.11-slim python -c \
  "from passlib.hash import bcrypt; print(bcrypt.hash('Admin@2026!'))"
```

Copy the output (it will look like `$2b$12$...` about 60 chars long). You will use it in the next step.

- [ ] **Step 2: Update 01_users.sql with real hash**

In `seeds/01_users.sql`, replace both `'$2b$12$PLACEHOLDER_BCRYPT_HASH'` values with your generated hash. Example (your hash will differ):

```sql
INSERT INTO users (user_name, password_hash, authority, control_authority, usr_group)
VALUES
  ('13800138000', '$2b$12$PASTE_YOUR_GENERATED_HASH_HERE', 'Administrators', 3, 'demo'),
  ('13800138001', '$2b$12$PASTE_YOUR_GENERATED_HASH_HERE', 'Company', 1, 'demo')
ON CONFLICT (user_name) DO NOTHING;
```

Use the same hash for both users (they share password `Admin@2026!` for local testing).

- [ ] **Step 3: Create scripts/ directory and write entrypoint-migrate.sh**

```bash
mkdir -p D:/江苏润盛/scripts
```

File `scripts/entrypoint-migrate.sh`:

```bash
#!/bin/bash
set -euo pipefail

echo "[migrate] Waiting for postgres to be ready..."
# alembic will fail fast if postgres is not accepting connections

echo "[migrate] Running alembic upgrade head..."
alembic upgrade head

echo "[migrate] Running seeds..."
python tools/run_seeds.py

echo "[migrate] Database initialised successfully."
```

- [ ] **Step 4: Make the script executable (Linux line endings)**

The script will run inside a Linux container, so it must have Unix line endings. Write it with `printf` to ensure no CRLF:

This is handled automatically by the Write tool if you write it correctly. Verify with:
```bash
file scripts/entrypoint-migrate.sh
```
Expected: `ASCII text` (not `with CRLF`).

If CRLF, fix with: `sed -i 's/\r//' scripts/entrypoint-migrate.sh`

- [ ] **Step 5: Commit**

```bash
git add scripts/entrypoint-migrate.sh seeds/01_users.sql
git commit -m "chore(deploy): migrate entrypoint script + seed real bcrypt hash"
```

---

## Task 5: docker-compose.prod.yml

**Files:**
- Create: `docker-compose.prod.yml`

- [ ] **Step 1: Write docker-compose.prod.yml**

```yaml
name: ruisheng-prod

services:
  postgres:
    image: timescale/timescaledb:2.16.1-pg15
    container_name: ruisheng-postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ruisheng
    volumes:
      - ruisheng-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ruisheng"]
      interval: 5s
      timeout: 5s
      retries: 12
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: ruisheng-redis
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - ruisheng-redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  migrate:
    build:
      context: .
      dockerfile: ruisheng-api/Dockerfile
    entrypoint: ["bash", "scripts/entrypoint-migrate.sh"]
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      # Superuser URL used by alembic (creates roles + timescaledb extension)
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/ruisheng
      RUISHENG_GW_PASSWORD: ${RUISHENG_GW_PASSWORD}
      RUISHENG_API_PASSWORD: ${RUISHENG_API_PASSWORD}
    restart: "no"

  api:
    build:
      context: .
      dockerfile: ruisheng-api/Dockerfile
    container_name: ruisheng-api
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
    environment:
      API_LISTEN_HOST: "0.0.0.0"
      API_LISTEN_PORT: "8000"
      API_DB_URL: postgresql+asyncpg://ruisheng_api:${RUISHENG_API_PASSWORD}@postgres:5432/ruisheng
      API_GW_DB_URL: postgresql+asyncpg://ruisheng_gw:${RUISHENG_GW_PASSWORD}@postgres:5432/ruisheng
      API_REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      API_JWT_SECRET: ${JWT_SECRET}
      API_ENV: prod
    # Not exposed directly — nginx proxies to it
    restart: unless-stopped

  gw:
    build:
      context: .
      dockerfile: ruisheng-gw/Dockerfile
    container_name: ruisheng-gw
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
    environment:
      GW_LISTEN_HOST: "0.0.0.0"
      GW_LISTEN_PORT: "5020"
      GW_DATABASE_URL: postgresql+asyncpg://ruisheng_gw:${RUISHENG_GW_PASSWORD}@postgres:5432/ruisheng
      GW_REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
    ports:
      - "5020:5020"  # Modbus TCP device connections
      - "9090:9090"  # GW health endpoint
    restart: unless-stopped

  web:
    build:
      context: ruisheng-web
      dockerfile: Dockerfile
    container_name: ruisheng-web
    depends_on:
      - api
    ports:
      - "80:80"
    restart: unless-stopped

volumes:
  ruisheng-pgdata:
  ruisheng-redisdata:
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.prod.yml
git commit -m "chore(deploy): add docker-compose.prod.yml"
```

---

## Task 6: .env.prod.example

**Files:**
- Create: `.env.prod.example`

- [ ] **Step 1: Write the template**

```bash
# ============================================================
# ruisheng-scada production environment — copy to .env.prod
# ============================================================
# NEVER commit .env.prod to git.
# Generate passwords with: openssl rand -base64 24

# ---- PostgreSQL superuser (used by alembic migrate service) ----
POSTGRES_USER=ruisheng_admin
POSTGRES_PASSWORD=CHANGE_ME_PG_SUPERUSER_PASSWORD

# ---- Application DB role passwords (set by alembic migration D3) ----
RUISHENG_GW_PASSWORD=CHANGE_ME_GW_ROLE_PASSWORD
RUISHENG_API_PASSWORD=CHANGE_ME_API_ROLE_PASSWORD

# ---- Redis ----
REDIS_PASSWORD=CHANGE_ME_REDIS_PASSWORD

# ---- JWT secret (minimum 32 characters, random) ----
# Generate: openssl rand -base64 32
JWT_SECRET=CHANGE_ME_JWT_SECRET_AT_LEAST_32_CHARS_RANDOM
```

- [ ] **Step 2: Commit**

```bash
git add .env.prod.example
git commit -m "chore(deploy): add .env.prod.example template"
```

---

## Task 7: Local smoke test

**Pre-requisites:** Docker Desktop running, ports 80, 5020, 9090 not in use locally.

- [ ] **Step 1: Create .env.prod from template**

```bash
cp .env.prod.example .env.prod
```

Open `.env.prod` and fill in real values. Use `openssl rand -base64 24` to generate passwords. Example values (change these):
```
POSTGRES_USER=ruisheng_admin
POSTGRES_PASSWORD=Pg_Adm1n_2026!Xyz
RUISHENG_GW_PASSWORD=Gw_R0le_2026!Abc
RUISHENG_API_PASSWORD=Api_R0le_2026!Def
REDIS_PASSWORD=Redis_2026!Ghi
JWT_SECRET=JWT_Secret_2026!Jkl_MnopQrstUvwx
```

- [ ] **Step 2: Build and start all services**

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up --build -d
```

Expected: All 6 services start. The `migrate` service will show `[migrate] Database initialised successfully.` in its logs.

- [ ] **Step 3: Watch migrate logs until complete**

```bash
docker compose -f docker-compose.prod.yml logs migrate -f
```

Expected output:
```
[migrate] Waiting for postgres to be ready...
[migrate] Running alembic upgrade head...
INFO  [alembic.runtime.migration] Running upgrade ...
...
[migrate] Running seeds...
[seed] 00_wx_groups.sql
[seed] 01_users.sql
[seed] 02_devices.sql
[seed] 03_device_points.sql
[migrate] Database initialised successfully.
```

- [ ] **Step 4: Check all services are healthy**

```bash
docker compose -f docker-compose.prod.yml ps
```

Expected: postgres, redis, api, gw, web all show `running` or `Up`. migrate shows `Exited (0)`.

- [ ] **Step 5: Smoke test — API health**

```bash
curl -s http://localhost/api/v1/__diag | python -m json.tool
```

Expected: JSON with `"status": "ok"` (or similar health response). If 404, check API route with:
```bash
curl -s http://localhost/api/v1/health 2>/dev/null || curl -s http://localhost/api/diag
```

- [ ] **Step 6: Smoke test — Web UI loads**

Open browser to `http://localhost`. Expected: 江苏润盛 SCADA login page loads.

- [ ] **Step 7: Smoke test — Login**

Log in with:
- Username: `13800138000`
- Password: `Admin@2026!`

Expected: Redirect to dashboard, device list visible with DEMO device.

- [ ] **Step 8: Smoke test — GW health**

```bash
curl -s http://localhost:9090/health
```

Expected: `{"status": "ok"}` or similar.

- [ ] **Step 9: Check logs for errors**

```bash
docker compose -f docker-compose.prod.yml logs api --tail=50
docker compose -f docker-compose.prod.yml logs gw --tail=20
```

Expected: No ERROR or CRITICAL lines. Normal startup logs only.

- [ ] **Step 10: Stop cleanly**

```bash
docker compose -f docker-compose.prod.yml down
```

---

## Task 8: Customer deployment package

**Files:**
- Create: `deploy/setup-customer.md`

Goal: customer machine only needs Docker installed. No source code, no Python, no pnpm.

- [ ] **Step 1: Export Docker images to tarballs**

On your local machine (after Task 7 passes):

```bash
mkdir -p deploy/images

docker save ruisheng-prod-api:latest -o deploy/images/ruisheng-api.tar
docker save ruisheng-prod-gw:latest -o deploy/images/ruisheng-gw.tar
docker save ruisheng-prod-web:latest -o deploy/images/ruisheng-web.tar
docker save timescale/timescaledb:2.16.1-pg15 -o deploy/images/timescaledb.tar
docker save redis:7-alpine -o deploy/images/redis.tar
```

Note: check the exact image names docker compose assigned with `docker images | grep ruisheng`. The compose name prefix is `ruisheng-prod` by default (from `name: ruisheng-prod` in compose file).

- [ ] **Step 2: Copy deployment files to deploy/ folder**

```bash
cp docker-compose.prod.yml deploy/
cp .env.prod.example deploy/
```

- [ ] **Step 3: Write deploy/setup-customer.md**

```markdown
# 江苏润盛 SCADA — 部署说明

## 前提条件

- Windows 10/11 或 Ubuntu 20.04+
- Docker Desktop（Windows）或 Docker Engine + Docker Compose v2（Linux）
- 80、5020、9090 端口未被占用

## 部署步骤

### 1. 加载 Docker 镜像

将 `images/` 目录中的所有 `.tar` 文件拷贝到目标机器，在该目录下运行：

```powershell
# Windows PowerShell
docker load -i images\timescaledb.tar
docker load -i images\redis.tar
docker load -i images\ruisheng-api.tar
docker load -i images\ruisheng-gw.tar
docker load -i images\ruisheng-web.tar
```

```bash
# Linux
for f in images/*.tar; do docker load -i "$f"; done
```

### 2. 配置环境变量

```bash
cp .env.prod.example .env.prod
```

用文本编辑器打开 `.env.prod`，填写所有 `CHANGE_ME_*` 占位符：

| 变量 | 说明 | 示例 |
|------|------|------|
| `POSTGRES_PASSWORD` | PostgreSQL 超级用户密码 | 随机强密码 |
| `RUISHENG_GW_PASSWORD` | 网关 DB 角色密码 | 随机强密码 |
| `RUISHENG_API_PASSWORD` | API DB 角色密码 | 随机强密码 |
| `REDIS_PASSWORD` | Redis 密码 | 随机强密码 |
| `JWT_SECRET` | JWT 签名密钥（≥32 字符） | 随机字符串 |

生成随机密码（在 Linux/Mac）：`openssl rand -base64 24`

Windows PowerShell：`[System.Web.Security.Membership]::GeneratePassword(24, 4)`

### 3. 启动系统

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

首次启动约需 1-2 分钟完成数据库初始化。查看进度：

```bash
docker compose -f docker-compose.prod.yml logs migrate -f
```

看到 `Database initialised successfully.` 后初始化完成。

### 4. 验证

- 浏览器打开 `http://<本机IP>` — 出现登录页
- 登录账号：`13800138000` / 密码：`Admin@2026!`

### 5. 日常管理

```bash
# 停止
docker compose -f docker-compose.prod.yml down

# 重启
docker compose -f docker-compose.prod.yml up -d

# 查看日志
docker compose -f docker-compose.prod.yml logs -f

# 备份数据库
docker exec ruisheng-postgres pg_dump -U ruisheng_admin ruisheng > backup_$(date +%Y%m%d).sql
```

### 6. RS485 串口设备（如有）

在 `docker-compose.prod.yml` 的 `gw` 服务中添加 devices 挂载：

```yaml
gw:
  devices:
    - /dev/ttyUSB0:/dev/ttyUSB0  # 按实际串口改
  environment:
    GW_SERIAL_PORTS: '[{"port":"/dev/ttyUSB0","baud_rate":9600}]'
```

Windows 串口（COM3 等）需要在 WSL2 中通过 usbipd 转发，或使用 Linux 系统部署。
```

- [ ] **Step 4: Commit all deploy files**

```bash
git add deploy/
git commit -m "chore(deploy): customer deployment package + setup guide"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Postgres + TimescaleDB: `timescale/timescaledb:2.16.1-pg15` matches dev compose
- ✅ Redis: `redis:7-alpine` with password, matches dev compose
- ✅ alembic migrations: migrate service runs `alembic upgrade head` with `DATABASE_URL` env var (matches `alembic/env.py` logic)
- ✅ DB role passwords: migrate service passes `RUISHENG_GW_PASSWORD` + `RUISHENG_API_PASSWORD` (required by migration `e74ffa548c2f`)
- ✅ API `extra=forbid`: only `API_*` vars set in api service environment; no unknown keys
- ✅ GW `extra=forbid`: only `GW_*` vars set in gw service environment
- ✅ API listens on 0.0.0.0 (not 127.0.0.1 default) — nginx can reach it
- ✅ GW serial optional dep installed: `--extra serial` in GW Dockerfile
- ✅ Vue build: `VITE_API_BASE=/api` and `VITE_WS_BASE=/ws` set before `pnpm build`
- ✅ Seeds: `run_seeds.py` uses `DATABASE_URL.replace("+asyncpg", "")` — works with asyncpg-format URL
- ✅ Seed login: placeholder bcrypt hash replaced with real hash in Task 4

**Placeholder scan:** No TBD/TODO items. All code blocks are complete.

**Type consistency:** Not applicable (no type-checked code in this plan — shell scripts and config files only).
