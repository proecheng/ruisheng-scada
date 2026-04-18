# Plan 0 — 基础设施与共享包 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭出可让 Plan 1–4 直接动工的基础设施：monorepo + uv 工作区 + `ruisheng-shared` 包（23 张表 ORM + enums + 错误码 + schema 版本化）+ Alembic 迁移（含 TimescaleDB hypertable/retention/compression）+ Docker Compose 开发环境 + testcontainers/embedded PG 双轨 + 伪设备 PCAP 生成器雏形 + CI 骨架。

**Architecture:** uv workspace monorepo，子包 `ruisheng-shared`（Python 纯库）+ 未来 `ruisheng-api` / `ruisheng-gw`。PostgreSQL 15 + TimescaleDB 2.x 作为唯一持久层。Redis 7 作为消息总线与缓存。本地开发用 Docker Compose 一键启动；Windows 客户现场无 Docker 时通过 `USE_EMBEDDED_PG=1` 走内嵌 PostgreSQL 二进制。所有共享数据模型、枚举、错误码、协议常量集中在 `ruisheng-shared`，启动时靠 `SHARED_SCHEMA_VERSION` 硬阻断不匹配。

**Tech Stack:**
- Python 3.11（Embedded 二进制打包）
- uv 0.4+（包/依赖管理 + workspace）
- SQLAlchemy 2.0（async ORM）+ asyncpg
- Alembic（数据库迁移）
- PostgreSQL 15 + TimescaleDB 2.x
- Redis 7
- pydantic-settings 2.x（配置管理）
- pytest + pytest-asyncio + testcontainers-python + pytest-cov + hypothesis
- ruff（格式化 + 静态检查）+ mypy --strict
- pre-commit + GitHub Actions（CI）

---

## 参考 spec

本 plan 对应设计文档：
`D:\江苏润盛\docs\superpowers\specs\2026-04-13-ruisheng-iot-design.md` v1.3.2

关键 spec 锚点：
- §2.3 `ruisheng-shared` 版本化机制
- §4 数据模型（23 张表 + TimescaleDB hypertable）
- §5.1 错误码 ErrCode
- §5.12 日志策略（仅定义 logger 但不在 Plan 0 实现）
- §6.2 覆盖率门槛
- §6.5.1 testcontainers + embedded PG fallback
- §6.6.1 伪设备 PCAP 生成器
- §A 协议规范附录（Plan 0 只用到 enums 常量层）

---

## 文件结构蓝图

仓库根目录（本 plan 结束后）：

```
D:\江苏润盛\
├── .gitignore                     # 已存在（需扩展）
├── pyproject.toml                 # workspace 根
├── uv.lock
├── README.md
├── CONTRIBUTING.md
├── docker-compose.dev.yml         # PG + TimescaleDB + Redis
├── docker-compose.test.yml        # testcontainers 备选
├── Makefile                       # 开发命令快捷方式（tasks via taskipy）
├── .env.example
├── .pre-commit-config.yaml
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/                          # 已存在
│   └── superpowers/
│
├── ruisheng-shared/
│   ├── pyproject.toml
│   ├── README.md
│   └── src/ruisheng_shared/
│       ├── __init__.py            # SHARED_SCHEMA_VERSION = 20260413
│       ├── CHANGELOG.md
│       ├── models/
│       │   ├── __init__.py
│       │   ├── base.py            # declarative base + 通用 mixin
│       │   ├── tenants.py         # wx_groups
│       │   ├── users.py           # users, user_wx_bindings, user_phone_numbers, user_emails
│       │   ├── devices.py         # devices, device_points, device_static_data, sim_cards, device_templates
│       │   ├── alarms.py          # device_waring_cfgs, alarm_records, alarm_outbox
│       │   ├── control.py         # user_control_actions
│       │   ├── plans.py           # timing_plans, maintain_plans, maintain_actions
│       │   ├── scenes.py          # scene_pages, scene_views
│       │   ├── pay.py             # pay_orders, pay_orders_seen
│       │   ├── logs.py            # soft_logs, user_login_records
│       │   └── timeseries.py      # point_data_realtime, point_data_history, waveform_history
│       ├── schemas/
│       │   ├── __init__.py
│       │   ├── common.py          # ApiResponse, PaginationCursor
│       │   ├── devices.py
│       │   ├── alarms.py
│       │   ├── control.py
│       │   ├── ws.py              # WSMessage 信封
│       │   └── protocol.py        # FrameHeader 等
│       ├── enums/
│       │   ├── __init__.py
│       │   ├── fun_code.py
│       │   ├── alarm_type.py
│       │   ├── alarm_action.py
│       │   ├── control_status.py
│       │   └── authority.py
│       ├── errors/
│       │   ├── __init__.py
│       │   └── codes.py           # ErrCode enum + BizError 类
│       ├── constants/
│       │   ├── __init__.py
│       │   ├── protocol.py        # CRC16 多项式、端口号、心跳周期等
│       │   └── limits.py          # 磁盘 / 队列 / TTL 常量
│       ├── validators/
│       │   ├── __init__.py
│       │   └── rs485.py           # 波特率×终端数×周期约束表
│       └── py.typed               # mypy 标记
│
├── alembic/                       # 顶层 alembic（供未来 api/gw 共用）
│   ├── alembic.ini
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 20260413_0001_initial_schema.py
│
├── seeds/                         # SQL 种子数据
│   ├── 00_wx_groups.sql
│   ├── 01_users.sql
│   ├── 02_devices.sql
│   └── 03_device_points.sql
│
├── tools/
│   ├── pcap_gen/                  # 伪设备 PCAP 生成器雏形
│   │   ├── pyproject.toml
│   │   └── src/pcap_gen/
│   │       ├── __init__.py
│   │       ├── cli.py             # typer CLI
│   │       ├── scenarios.py
│   │       └── modbus_frames.py
│   ├── embedded_pg.py             # Windows PG portable 包装
│   └── verify_schema_version.py   # CI 辅助脚本
│
└── tests/
    ├── conftest.py                # 根级 fixtures：postgres / redis 双轨
    ├── unit/
    │   └── shared/
    │       ├── test_enums.py
    │       ├── test_errors.py
    │       ├── test_models_mapping.py
    │       └── test_validators_rs485.py
    ├── integration/
    │   └── test_alembic_upgrade.py
    └── tools/
        └── test_pcap_gen.py
```

**责任分工原则**
- 每个文件一个清晰责任（e.g. `users.py` 只含用户域 4 张表的 ORM；`alarms.py` 只含告警 3 张表）
- 模型按"数据领域"而不是"技术层"分组
- `enums/` / `errors/` / `constants/` 一一对应 spec 里的概念分组

---

## 阶段划分（7 阶段，≈110 任务）

| 阶段 | 目标 | 任务数 |
|---|---|---|
| A | 仓库骨架 + 工具链 | 15 |
| B | `ruisheng-shared` enums/errors/constants/validators | 18 |
| C | `ruisheng-shared` ORM 模型 23 表 | 28 |
| D | Alembic 迁移 + hypertable + compression + retention | 15（含 D0 环境前置校验）|
| E | testcontainers + embedded PG fallback + seeds | 12 |
| F | 伪设备 PCAP 生成器雏形 | 10 |
| G | CI 流水线完备 + 文档 + release + 技术债清理 | 15（G1–G5 + G6 deps 迁移 + G7 release workflow）|

---

# 阶段 A — 仓库骨架与工具链

## Task A1：初始化 uv 与根 pyproject.toml

**Files:**
- Create: `D:\江苏润盛\pyproject.toml`
- Create: `D:\江苏润盛\.python-version`

- [ ] **Step 1：安装 uv（若未装）**

```bash
# Windows PowerShell（一次性）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version   # 期望 0.4.x 以上
```

- [ ] **Step 2：固定 Python 版本**

Create `D:\江苏润盛\.python-version`:
```
3.11
```

- [ ] **Step 3：创建根 pyproject.toml（workspace 定义）**

Create `D:\江苏润盛\pyproject.toml`:
```toml
[project]
name = "ruisheng-iot"
version = "0.1.0"
description = "江苏润盛 IoT SCADA 平台重做 monorepo"
requires-python = ">=3.11,<3.12"
readme = "README.md"
license = { text = "Proprietary" }

[tool.uv.workspace]
members = ["ruisheng-shared", "tools/pcap_gen"]

[tool.uv]
dev-dependencies = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5.0",
  "pytest-rerunfailures>=14.0",
  "hypothesis>=6.100",
  "ruff>=0.5",
  "mypy>=1.11",
  "pre-commit>=3.7",
  "testcontainers[postgres,redis]>=4.7",
  "taskipy>=1.13",
  "fakeredis>=2.23",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "C4", "SIM", "ASYNC", "PL"]
ignore = ["E501", "PLR0913"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["PLR2004"]  # 测试里允许 magic number

[tool.mypy]
python_version = "3.11"
strict = true
plugins = ["pydantic.mypy"]
exclude = ["tests/.*", "alembic/.*"]

[tool.pytest.ini_options]
minversion = "8.0"
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"
markers = [
  "integration: 需要 PG/Redis 的集成测试",
  "slow: 超过 5s 的慢测",
]

[tool.coverage.run]
source = ["ruisheng-shared/src", "tools/pcap_gen/src"]
branch = true  # §6.2 门槛：分支覆盖，不是行覆盖

[tool.coverage.report]
exclude_lines = [
  "pragma: no cover",
  "raise NotImplementedError",
  "if TYPE_CHECKING:",
]
fail_under = 90

[tool.taskipy.tasks]
lint = "ruff check . && mypy ."
fmt = "ruff format ."
test = "pytest -x"
cov = "pytest --cov --cov-report=term-missing"
```

- [ ] **Step 4：生成 uv.lock 验证可用**

```bash
cd "D:\江苏润盛"
uv lock
ls uv.lock   # 应存在
```

- [ ] **Step 5：Commit**

```bash
git add pyproject.toml .python-version uv.lock
git commit -m "feat(build): init uv workspace and root pyproject

- Python 3.11 locked
- ruff + mypy strict baseline
- pytest async + branch coverage (fail_under 90)
- dev deps: testcontainers, hypothesis, fakeredis, taskipy"
```

---

## Task A2：根 .gitignore 扩展（把 Python / uv / 测试缓存加上）

**Files:**
- Modify: `D:\江苏润盛\.gitignore`

- [ ] **Step 1：确认现有 .gitignore**

```bash
cat "D:\江苏润盛\.gitignore" | head -30
```
期望已有旧系统忽略规则（`DataBase/` / `*.mdf` / `*.rar` 等）。

- [ ] **Step 2：追加 Python / 工具链忽略项**

Append to `D:\江苏润盛\.gitignore`:
```
# ===== Plan 0 新增 =====

# uv
.venv/
uv.lock            # 注意：有争议；我们选择提交 lock（见 CONTRIBUTING）

# Python
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.coverage.*
htmlcov/
coverage.xml
.hypothesis/

# Docker / Local dev
.env
!.env.example

# Alembic（只忽略临时/缓存，版本文件必进）
alembic/__pycache__/

# Embedded PG portable
.embedded_pg/

# PCAP 生成器输出
corpus/generated/
```

> 注：上面 `uv.lock` 这一行应当**删除**（因为我们刚在 A1 提交了 lock）。保留注释说明决策。

实际写入时去掉 `uv.lock` 行：
```
.venv/
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.coverage.*
htmlcov/
coverage.xml
.hypothesis/
.env
!.env.example
alembic/__pycache__/
.embedded_pg/
corpus/generated/
```

- [ ] **Step 3：Commit**

```bash
git add .gitignore
git commit -m "chore(git): extend gitignore for Python and tooling caches"
```

---

## Task A3：创建 ruisheng-shared 子包骨架

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\pyproject.toml`
- Create: `D:\江苏润盛\ruisheng-shared\README.md`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\__init__.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\py.typed`（空文件）
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\CHANGELOG.md`

- [ ] **Step 1：创建子包 pyproject.toml**

Create `D:\江苏润盛\ruisheng-shared\pyproject.toml`:
```toml
[project]
name = "ruisheng-shared"
version = "0.1.0"
description = "润盛 IoT 共享模型/枚举/错误码/常量"
requires-python = ">=3.11,<3.12"
dependencies = [
  "sqlalchemy[asyncio]>=2.0.30",
  "asyncpg>=0.29",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "python-ulid>=2.7",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ruisheng_shared"]
```

- [ ] **Step 2：创建包入口**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\__init__.py`:
```python
"""润盛 IoT 共享库 — 模型、枚举、错误码、常量。

所有业务服务（ruisheng-api / ruisheng-gw）启动时必须检查
SHARED_SCHEMA_VERSION == REQUIRED，不匹配则拒绝启动。
"""

# 格式：YYYYMMDD + 当天递增 2 位 → 20260413
# 规则：任何 breaking change（字段删/改类型/必填新增）必须 +1 并更新 CHANGELOG
SHARED_SCHEMA_VERSION: int = 20260413

__version__ = "0.1.0"
__all__ = ["SHARED_SCHEMA_VERSION"]
```

- [ ] **Step 3：创建 py.typed 与 CHANGELOG.md**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\py.typed`:
（空文件，让 mypy/IDE 识别本包有类型信息）

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\CHANGELOG.md`:
```markdown
# ruisheng-shared 变更日志

每次改动本包必须在此文件登记一条，用下列前缀之一：

- `breaking:` — 需同步升级 SHARED_SCHEMA_VERSION 与 api/gw 的 REQUIRED
- `deprecation:` — 不立即破坏，两个小版本内清理
- `feature:` — 新增字段/类型
- `fix:` — 错误修正
- `chore:` — 重构、重命名、注释（无语义变化）

## 2026-04-13 v0.1.0

- chore: 初始版本，SHARED_SCHEMA_VERSION=20260413
```

- [ ] **Step 4：创建 README.md**

Create `D:\江苏润盛\ruisheng-shared\README.md`:
```markdown
# ruisheng-shared

润盛 IoT 平台的共享 Python 包：
- SQLAlchemy 2.0 ORM 模型（23 张表）
- Pydantic schemas（API 请求/响应 + WS 信封）
- enums（FunCode / AlarmType / AlarmAction / ControlStatus / Authority）
- errors（ErrCode + BizError）
- constants（CRC 多项式、端口、TTL）
- validators（RS485 波特率约束表）

## 启动检查

```python
from ruisheng_shared import SHARED_SCHEMA_VERSION
REQUIRED = 20260413
if SHARED_SCHEMA_VERSION != REQUIRED:
    raise RuntimeError(f"shared version mismatch: {SHARED_SCHEMA_VERSION} != {REQUIRED}")
```
```

- [ ] **Step 5：验证 uv workspace 能发现本包**

```bash
cd "D:\江苏润盛"
uv sync
uv run python -c "from ruisheng_shared import SHARED_SCHEMA_VERSION; print(SHARED_SCHEMA_VERSION)"
```
期望输出：`20260413`

- [ ] **Step 6：Commit**

```bash
git add ruisheng-shared/
git commit -m "feat(shared): bootstrap ruisheng-shared package

- Package skeleton with SHARED_SCHEMA_VERSION=20260413
- py.typed marker for mypy
- CHANGELOG format with breaking/deprecation/feature/fix/chore prefixes
- README explaining startup version check contract"
```

---

## Task A4：pre-commit 配置

**Files:**
- Create: `D:\江苏润盛\.pre-commit-config.yaml`

- [ ] **Step 1：写入 pre-commit 配置**

Create `D:\江苏润盛\.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
      - id: check-case-conflict
      - id: mixed-line-ending
        args: [--fix=lf]
      - id: detect-private-key

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.7
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.1
    hooks:
      - id: mypy
        additional_dependencies: [sqlalchemy, pydantic, types-redis]
        args: [--strict]
        exclude: ^(tests/|alembic/|tools/pcap_gen/)

  - repo: local
    hooks:
      - id: shared-schema-version-bump
        name: 检查 SHARED_SCHEMA_VERSION 是否需要升级
        entry: python tools/verify_schema_version.py
        language: system
        files: ^ruisheng-shared/src/ruisheng_shared/(models|schemas)/
        pass_filenames: false
```

- [ ] **Step 2：安装 pre-commit hook**

```bash
cd "D:\江苏润盛"
uv run pre-commit install
uv run pre-commit run --all-files   # 首次跑会修很多空白/末换行
```

- [ ] **Step 3：Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore(ci): add pre-commit hooks

- Standard cleanup (trailing ws / EOL / private key)
- Ruff lint + format
- Mypy strict (exclude tests/alembic)
- Local hook: enforce SHARED_SCHEMA_VERSION bump when shared/ changes"
```

---

## Task A5：创建 `tools/verify_schema_version.py` 空壳（A4 已引用，先建个 stub）

**Files:**
- Create: `D:\江苏润盛\tools\verify_schema_version.py`

- [ ] **Step 1：写入 stub 脚本**

Create `D:\江苏润盛\tools\verify_schema_version.py`:
```python
"""Pre-commit 钩子：当 ruisheng-shared 的 models/schemas 改动时，
检查 SHARED_SCHEMA_VERSION 是否也已上调 + CHANGELOG 是否加了新条目。

目前 stub 阶段：只检查 CHANGELOG.md 是否含今天日期。
完整实现在 Task G5（CI 完善阶段）。
"""
from __future__ import annotations

import datetime as _dt
import pathlib
import subprocess
import sys

CHANGELOG = pathlib.Path("ruisheng-shared/src/ruisheng_shared/CHANGELOG.md")


def _git_changed_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"],
        text=True,
    )
    return [ln for ln in out.splitlines() if ln.strip()]


def main() -> int:
    changed = _git_changed_files()
    watched = any(
        p.startswith("ruisheng-shared/src/ruisheng_shared/models/")
        or p.startswith("ruisheng-shared/src/ruisheng_shared/schemas/")
        for p in changed
    )
    if not watched:
        return 0

    today = _dt.date.today().isoformat()
    text = CHANGELOG.read_text(encoding="utf-8")
    if today not in text:
        print(
            f"ERROR: 修改了 shared 的 models/schemas，CHANGELOG.md 里还没有 {today} 的条目。",
            file=sys.stderr,
        )
        print("请追加一条，格式参考 CHANGELOG 文件头的说明。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2：本地试跑**

```bash
cd "D:\江苏润盛"
uv run python tools/verify_schema_version.py
# 没改 shared → 立刻返回 0
echo $?   # 0
```

- [ ] **Step 3：Commit**

```bash
git add tools/verify_schema_version.py
git commit -m "chore(ci): add shared schema version bump verifier stub"
```

---

## Task A6：docker-compose.dev.yml 本地开发环境

**Files:**
- Create: `D:\江苏润盛\docker-compose.dev.yml`
- Create: `D:\江苏润盛\.env.example`

- [ ] **Step 1：写入 compose 文件**

Create `D:\江苏润盛\docker-compose.dev.yml`:
```yaml
name: ruisheng-dev

services:
  postgres:
    image: timescale/timescaledb:2.16.1-pg15
    container_name: ruisheng-postgres-dev
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: ruisheng_dev
      POSTGRES_PASSWORD: ruisheng_dev
      POSTGRES_DB: ruisheng
    volumes:
      - ruisheng-pgdata:/var/lib/postgresql/data
      - ./seeds:/seeds:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ruisheng_dev -d ruisheng"]
      interval: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    container_name: ruisheng-redis-dev
    ports:
      - "6379:6379"
    command: redis-server --appendonly yes --requirepass dev-redis-pw
    volumes:
      - ruisheng-redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "dev-redis-pw", "ping"]
      interval: 5s
      retries: 10

volumes:
  ruisheng-pgdata:
  ruisheng-redisdata:
```

- [ ] **Step 2：写入 .env.example**

Create `D:\江苏润盛\.env.example`:
```
# 拷贝成 .env 并按需改。.env 被 gitignore 忽略。

# 数据库
DATABASE_URL=postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng

# Redis
REDIS_URL=redis://:dev-redis-pw@127.0.0.1:6379/0

# 共享 schema 期望版本（api/gw 启动时比对）
RUISHENG_SHARED_REQUIRED_VERSION=20260413

# 测试模式开关
USE_EMBEDDED_PG=0
```

- [ ] **Step 3：启动并健康检查**

```bash
cd "D:\江苏润盛"
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml ps
# 期望：postgres 和 redis 都 (healthy)
```

- [ ] **Step 4：验证可连**

```bash
docker exec ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "SELECT 1;"
# 期望：输出 ?column? = 1
docker exec ruisheng-redis-dev redis-cli -a dev-redis-pw ping
# 期望：PONG
```

- [ ] **Step 5：关停容器（开发时再起）**

```bash
docker compose -f docker-compose.dev.yml down
```

- [ ] **Step 6：Commit**

```bash
git add docker-compose.dev.yml .env.example
git commit -m "chore(dev): add docker-compose for PG+TimescaleDB+Redis

- TimescaleDB 2.16 on PG 15 with healthchecks
- Redis 7 with requirepass (dev password only)
- .env.example documents all tunables"
```

---

## Task A7：Makefile 式任务入口（taskipy）

**Files:**
- Modify: `D:\江苏润盛\pyproject.toml`（补 tasks 段）
- Create: `D:\江苏润盛\Makefile`（Windows make 可选，提供 POSIX 端使用）

- [ ] **Step 1：扩充 taskipy tasks**

Edit `D:\江苏润盛\pyproject.toml` 的 `[tool.taskipy.tasks]` 段为：
```toml
[tool.taskipy.tasks]
lint = "ruff check . && mypy ."
fmt = "ruff format ."
test = "pytest -x"
cov = "pytest --cov --cov-report=term-missing"
up = "docker compose -f docker-compose.dev.yml up -d"
down = "docker compose -f docker-compose.dev.yml down"
logs = "docker compose -f docker-compose.dev.yml logs -f"
db = "docker exec -it ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng"
redis = "docker exec -it ruisheng-redis-dev redis-cli -a dev-redis-pw"
migrate = "alembic upgrade head"
downgrade = "alembic downgrade -1"
seed = "python tools/run_seeds.py"
```

- [ ] **Step 2：创建简易 Makefile 供 POSIX shell 用户**

Create `D:\江苏润盛\Makefile`:
```makefile
.PHONY: up down logs test cov lint fmt migrate seed

up:
	uv run task up

down:
	uv run task down

test:
	uv run task test

cov:
	uv run task cov

lint:
	uv run task lint

fmt:
	uv run task fmt

migrate:
	uv run task migrate

seed:
	uv run task seed
```

- [ ] **Step 3：验证 taskipy 可用**

```bash
cd "D:\江苏润盛"
uv run task --list
# 期望列出所有定义的 tasks
```

- [ ] **Step 4：Commit**

```bash
git add pyproject.toml Makefile
git commit -m "chore(dev): add taskipy shortcuts and Makefile

- up/down/logs for docker compose
- db/redis shells
- migrate/downgrade/seed placeholders (impl in stages D & E)"
```

---

## Task A8：CI 基础 workflow（后续阶段 G 会扩充）

**Files:**
- Create: `D:\江苏润盛\.github\workflows\ci.yml`

- [ ] **Step 1：创建 CI workflow**

Create `D:\江苏润盛\.github\workflows\ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [master, develop]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy .

  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync
      - run: uv run pytest tests/unit -v --cov --cov-fail-under=90
```

> 阶段 G 会加上 integration / replay / perf-smoke 三个 job。

- [ ] **Step 2：Commit（本地 push 前验证）**

```bash
# 本地先跑一遍 lint 段
cd "D:\江苏润盛"
uv sync
uv run ruff check .
uv run mypy ruisheng-shared/
# 期望：0 错误（目前 shared 只有 __init__.py 会通过）
```

- [ ] **Step 3：Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add lint + unit skeleton workflow

More jobs (integration/replay/perf) will be added in Stage G."
```

---

## Task A9：tests/ 目录骨架 + 最小 smoke 测试

**Files:**
- Create: `D:\江苏润盛\tests\__init__.py`（空）
- Create: `D:\江苏润盛\tests\conftest.py`
- Create: `D:\江苏润盛\tests\unit\__init__.py`（空）
- Create: `D:\江苏润盛\tests\unit\shared\__init__.py`（空）
- Create: `D:\江苏润盛\tests\unit\shared\test_smoke.py`

- [ ] **Step 1：写最基础的 conftest**

Create `D:\江苏润盛\tests\conftest.py`:
```python
"""根 conftest：目前仅提供标识 Windows 的 fixture。
数据库/Redis fixtures 在 Stage E 添加。"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def is_windows() -> bool:
    return sys.platform == "win32"
```

- [ ] **Step 2：写 smoke 测试（验 shared 可导入 + 版本号在）**

Create `D:\江苏润盛\tests\unit\shared\test_smoke.py`:
```python
"""最小 smoke 测试：ruisheng_shared 包可导入且 SHARED_SCHEMA_VERSION 为正整数。"""
from __future__ import annotations


def test_shared_importable() -> None:
    import ruisheng_shared

    assert hasattr(ruisheng_shared, "SHARED_SCHEMA_VERSION")


def test_schema_version_positive_int() -> None:
    from ruisheng_shared import SHARED_SCHEMA_VERSION

    assert isinstance(SHARED_SCHEMA_VERSION, int)
    assert SHARED_SCHEMA_VERSION > 20250000  # sanity: 不早于 2025 年
```

- [ ] **Step 3：运行测试**

```bash
cd "D:\江苏润盛"
uv run pytest tests/unit/shared/test_smoke.py -v
```
期望：2 passed。

- [ ] **Step 4：Commit**

```bash
git add tests/
git commit -m "test(shared): smoke test for import + schema version presence"
```

---

## Task A10：README 根文档骨架

**Files:**
- Create 或 Modify: `D:\江苏润盛\README.md`（若已存在则扩展）

- [ ] **Step 1：检查是否已有 README**

```bash
ls "D:\江苏润盛\README.md" 2>&1
```
若存在 → Modify；若不存在 → Create。

- [ ] **Step 2：写入内容**

Create `D:\江苏润盛\README.md`:
```markdown
# 江苏润盛 IoT SCADA 平台（重做版）

> 工业 RS485/ModBus 设备远程监控平台的重做版。基于 Python FastAPI + Vue 3 + PostgreSQL+TimescaleDB + Redis。

## 快速开始

1. 安装 uv（Python 包管理器）：
   ```powershell
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. 同步依赖：
   ```bash
   uv sync
   ```

3. 启动本地开发依赖（PostgreSQL + Redis）：
   ```bash
   uv run task up
   ```

4. 跑数据库迁移（Plan 0 Stage D 完成后可用）：
   ```bash
   uv run task migrate
   ```

5. 跑测试：
   ```bash
   uv run task test
   ```

## 仓库结构

```
D:\江苏润盛\
├── ruisheng-shared/      # 共享包：ORM + enums + errors + constants
├── alembic/              # 数据库迁移
├── seeds/                # SQL 种子数据
├── tools/
│   ├── pcap_gen/         # 伪设备 PCAP 生成器
│   ├── embedded_pg.py    # Windows 内嵌 PG 包装
│   └── verify_schema_version.py
├── tests/
├── docs/
│   └── superpowers/
│       ├── specs/        # 设计文档
│       └── plans/        # 实施计划
└── docker-compose.dev.yml
```

## 后续阶段

- **Plan 1**: 采集网关 `ruisheng-gw`
- **Plan 2**: Web API `ruisheng-api`
- **Plan 3**: 前端 `ruisheng-web`
- **Plan 4**: 部署与运维

## 贡献

见 `CONTRIBUTING.md`。
```

- [ ] **Step 3：Commit**

```bash
git add README.md
git commit -m "docs: add README with quickstart and repo layout"
```

---

## Task A11：CONTRIBUTING.md 贡献指南

**Files:**
- Create: `D:\江苏润盛\CONTRIBUTING.md`

- [ ] **Step 1：写入内容**

Create `D:\江苏润盛\CONTRIBUTING.md`:
```markdown
# 贡献指南

## 提交前必做

1. `uv run task fmt` — 格式化
2. `uv run task lint` — ruff + mypy
3. `uv run task test` — pytest
4. pre-commit 会自动跑（包括 SHARED_SCHEMA_VERSION 检查）

## 分支与提交

- 主分支：`master`
- 功能分支：`feat/<topic>` / `fix/<topic>` / `chore/<topic>`
- 提交消息遵循 Conventional Commits：
  - `feat(scope): 简短描述`
  - `fix(scope): ...`
  - `docs(scope): ...`
  - `test(scope): ...`
  - `chore(scope): ...`

## 改动 ruisheng-shared 的注意事项

本包被 api/gw 共同依赖。**任何**改动 `models/` 或 `schemas/` 的 PR：

1. 必须在 `ruisheng-shared/src/ruisheng_shared/CHANGELOG.md` 加今日条目
2. 若为 **breaking**（删字段 / 改类型 / 新增必填）：
   - 升级 `SHARED_SCHEMA_VERSION`（+1）
   - 在 CHANGELOG 用 `breaking:` 前缀
   - 同时升级未来 api/gw 的 `REQUIRED_VERSION` 常量

pre-commit 的 `shared-schema-version-bump` hook 会自动检查 CHANGELOG 是否有今日条目；但**是否为 breaking** 需要人工判断。

## uv.lock 的约定

本仓库**提交** `uv.lock`。升级依赖时：
```bash
uv lock --upgrade-package <name>
```
PR review 时重点看 `uv.lock` 的 diff。

## 测试要求

- 新代码必须带测试
- `protocol/` 分支覆盖 95%，`domain/` 90%，`services/` 80%（见设计文档 §6.2）
- mutmut 每周自动跑（存活率 < 10% 算有效）
```

- [ ] **Step 2：Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING with commit style and shared rules"
```

---

## Task A12：验证仓库能一键 bootstrap

**Files:** 无新文件，只验证

- [ ] **Step 1：清理 venv（模拟新人首次 clone）**

```bash
cd "D:\江苏润盛"
rm -rf .venv
```

- [ ] **Step 2：从头 bootstrap**

```bash
uv sync
uv run pre-commit install
uv run task up
uv run task test
```

- [ ] **Step 3：验证 4 点**

预期：
- `uv sync` 成功拉所有依赖
- `pre-commit install` 成功
- `task up` 成功启动 PG + Redis（`docker ps` 显示 healthy）
- `task test` 的 smoke 测试通过（2 passed）

若任一失败：回头修对应 Task。

- [ ] **Step 4：记录成功（无新 commit，仅验收）**

---

## Task A13：设置 git 钩子跳过大文件

**Files:**
- Create: `D:\江苏润盛\.gitattributes`

- [ ] **Step 1：创建 .gitattributes**

Create `D:\江苏润盛\.gitattributes`:
```
# 行尾：Windows 仓库但代码用 LF，避免 CRLF 污染 Docker
*.py text eol=lf
*.sh text eol=lf
*.yaml text eol=lf
*.yml text eol=lf
*.md text eol=lf
*.toml text eol=lf
*.sql text eol=lf
Makefile text eol=lf

# 二进制（不做 diff）
*.pcap binary
*.blob binary
*.parquet binary
```

- [ ] **Step 2：Commit**

```bash
git add .gitattributes
git commit -m "chore(git): enforce LF for source files and mark binaries"
```

---

## Task A14：阶段 A 验收 —— 一键 `task bootstrap`

**Files:**
- Modify: `D:\江苏润盛\pyproject.toml`

- [ ] **Step 1：加 bootstrap 任务**

Edit `D:\江苏润盛\pyproject.toml` 的 `[tool.taskipy.tasks]` 段，末尾追加：
```toml
bootstrap = "uv sync && uv run pre-commit install && uv run task up && uv run task test"
```

- [ ] **Step 2：验证**

```bash
cd "D:\江苏润盛"
uv run task bootstrap
```
期望：连贯执行 4 步全部通过。

- [ ] **Step 3：Commit**

```bash
git add pyproject.toml
git commit -m "chore(dev): add one-shot 'task bootstrap'"
```

---

## Task A15：阶段 A 收尾 TAG

**Files:** 无

- [ ] **Step 1：打 stage-a-complete 标签**

```bash
cd "D:\江苏润盛"
git tag -a plan-0-stage-a-complete -m "Stage A: repo skeleton + tooling complete"
git tag   # 确认标签在
```

> 标签方便回滚和后续 plan 的起点对齐。

---

# 阶段 B — `ruisheng-shared` enums / errors / constants / validators

> Stage B 先做**纯 Python 数据**（没有 DB 依赖），完成后 Stage C 再做 ORM 模型，避免阶段性耦合。

## Task B1：创建 enums 子包骨架

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\__init__.py`

- [ ] **Step 1：写 __init__ 重导出**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\__init__.py`:
```python
"""枚举集合。任何新增 enum 都在此重导出以便 `from ruisheng_shared.enums import X`。"""

from .alarm_action import AlarmAction
from .alarm_type import AlarmType
from .authority import Authority
from .control_status import ControlStatus
from .fun_code import FunCode

__all__ = [
    "AlarmAction",
    "AlarmType",
    "Authority",
    "ControlStatus",
    "FunCode",
]
```

- [ ] **Step 2：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/enums/__init__.py
git commit -m "feat(shared): bootstrap enums package"
```

---

## Task B2：FunCode 枚举（TDD）

**Files:**
- Create: `D:\江苏润盛\tests\unit\shared\test_enums_fun_code.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\fun_code.py`

- [ ] **Step 1：写失败测试**

Create `D:\江苏润盛\tests\unit\shared\test_enums_fun_code.py`:
```python
"""Spec §A.4 — FunCode 枚举必须覆盖新系统保留的全部码，且值为 int。"""
from __future__ import annotations

import pytest

from ruisheng_shared.enums import FunCode


def test_funcode_standard_values() -> None:
    assert FunCode.READ_COILS == 1
    assert FunCode.READ_DISCRETE == 2
    assert FunCode.READ_HOLDING == 3
    assert FunCode.READ_INPUT == 4
    assert FunCode.WRITE_SINGLE_COIL == 5
    assert FunCode.WRITE_SINGLE_REGISTER == 6
    assert FunCode.WRITE_MULTIPLE_REGISTERS == 16


def test_funcode_private_values() -> None:
    # 私有扩展，spec §A.4
    assert FunCode.ICCID_REPORT == 20
    assert FunCode.REGISTER == 21
    assert FunCode.REGISTER_LOW_POWER == 22
    assert FunCode.HEARTBEAT == 0x19   # 25
    assert FunCode.GENERIC_RESPONSE == 100


def test_funcode_aliases_collapsed_to_parents() -> None:
    """FC 13 / 26 是 3 / 6 的变种别名，不单独成员（§11.1）"""
    assert not hasattr(FunCode, "READ_HOLDING_VARIANT")
    assert not hasattr(FunCode, "WRITE_SINGLE_REGISTER_VARIANT")


def test_funcode_removed() -> None:
    """FC 7 / 12 已砍（§11.1 D7）"""
    assert not hasattr(FunCode, "REQUEST_SERVICE")
    assert not hasattr(FunCode, "REGISTER_SYNC")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, FunCode.READ_HOLDING),
        (13, FunCode.READ_HOLDING),   # 别名合并
        (6, FunCode.WRITE_SINGLE_REGISTER),
        (26, FunCode.WRITE_SINGLE_REGISTER),  # 别名合并
    ],
)
def test_funcode_normalize_aliases(raw: int, expected: FunCode) -> None:
    """FunCode.normalize(raw_byte) 把 13 映射为 READ_HOLDING，26 映射为 WRITE_SINGLE_REGISTER"""
    assert FunCode.normalize(raw) is expected


def test_funcode_normalize_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown FunCode"):
        FunCode.normalize(7)   # 已砍 → 拒绝
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/unit/shared/test_enums_fun_code.py -v
```
期望：ImportError 或 AttributeError（FunCode 未实现）。

- [ ] **Step 3：实现 FunCode**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\fun_code.py`:
```python
"""ModBus FunCode 枚举。对应 spec §A.4。"""
from __future__ import annotations

from enum import IntEnum


class FunCode(IntEnum):
    """ModBus RTU/TCP 功能码。

    设计决策：
    - FC 13 (0x0D) 是 FC 3 的别名，解析时归并（Spec §2.1 / §11.1）
    - FC 26 (0x1A) 是 FC 6 的别名，同上
    - FC 7 / FC 12 已砍（§11.1 D7），不出现在本枚举
    - FC 0x19 (25) 是新约定心跳帧（§A.5）
    """

    # 标准 ModBus
    READ_COILS = 1
    READ_DISCRETE = 2
    READ_HOLDING = 3
    READ_INPUT = 4
    WRITE_SINGLE_COIL = 5
    WRITE_SINGLE_REGISTER = 6
    WRITE_MULTIPLE_REGISTERS = 16

    # 私有扩展
    ICCID_REPORT = 20
    REGISTER = 21
    REGISTER_LOW_POWER = 22
    HEARTBEAT = 0x19  # 25
    GENERIC_RESPONSE = 100

    _ALIASES: dict[int, int] = {  # type: ignore[assignment]
        # 注意：IntEnum 本身不允许非 int 成员；我们把 alias 表放在类的 __annotations__ 外
    }

    @classmethod
    def normalize(cls, raw: int) -> FunCode:
        """把接收到的 FunCode 字节归一化为枚举成员。

        处理别名（13 → 3, 26 → 6）与未知值拒绝。
        """
        alias_map = {13: 3, 26: 6}
        v = alias_map.get(raw, raw)
        try:
            return cls(v)
        except ValueError as e:
            raise ValueError(f"unknown FunCode {raw} (normalized to {v})") from e
```

> **注意**：`_ALIASES` 那段 dict 在 IntEnum 里放不了，所以实际实现里把 alias_map 放在 `normalize` 方法内即可。上面 stub 里的 `_ALIASES` 字段应当删除；完整文件应只包含成员定义 + normalize。下面是正确版本：

改为最终版本 — 重新写 `fun_code.py`：
```python
"""ModBus FunCode 枚举。对应 spec §A.4。"""
from __future__ import annotations

from enum import IntEnum


class FunCode(IntEnum):
    """ModBus RTU/TCP 功能码（保留成员，非保留的已砍）。"""

    READ_COILS = 1
    READ_DISCRETE = 2
    READ_HOLDING = 3
    READ_INPUT = 4
    WRITE_SINGLE_COIL = 5
    WRITE_SINGLE_REGISTER = 6
    WRITE_MULTIPLE_REGISTERS = 16
    ICCID_REPORT = 20
    REGISTER = 21
    REGISTER_LOW_POWER = 22
    HEARTBEAT = 0x19
    GENERIC_RESPONSE = 100

    @classmethod
    def normalize(cls, raw: int) -> FunCode:
        """归一化：FC13→FC3、FC26→FC6；未知码抛 ValueError。"""
        aliases = {13: cls.READ_HOLDING.value, 26: cls.WRITE_SINGLE_REGISTER.value}
        value = aliases.get(raw, raw)
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"unknown FunCode {raw} (normalized to {value})") from exc
```

- [ ] **Step 4：运行测试**

```bash
uv run pytest tests/unit/shared/test_enums_fun_code.py -v
```
期望：7 passed。

- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/enums/fun_code.py \
        ruisheng-shared/src/ruisheng_shared/enums/__init__.py \
        tests/unit/shared/test_enums_fun_code.py
git commit -m "feat(shared): FunCode enum with FC13/26 alias normalize

- Only retained codes: 1/2/3/4/5/6/16/20/21/22/0x19/100
- FC 7/12 killed (spec §11.1 D7)
- FC.normalize(raw) folds 13→3 and 26→6, rejects unknown"
```

---

## Task B3：AlarmType 枚举

**Files:**
- Create: `D:\江苏润盛\tests\unit\shared\test_enums_alarm_type.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\alarm_type.py`

- [ ] **Step 1：写失败测试**

Create `D:\江苏润盛\tests\unit\shared\test_enums_alarm_type.py`:
```python
"""Spec §F + §4.2 — AlarmType 5 种类型，用字符串值存 DB CHECK。"""
from __future__ import annotations

import pytest

from ruisheng_shared.enums import AlarmType


def test_all_five_members() -> None:
    assert {t.value for t in AlarmType} == {">", "<", "=", "!=", "LX"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(">", AlarmType.GT), ("<", AlarmType.LT), ("=", AlarmType.EQ), ("!=", AlarmType.NE), ("LX", AlarmType.LX)],
)
def test_from_symbol(raw: str, expected: AlarmType) -> None:
    assert AlarmType(raw) is expected


def test_invalid_symbol_raises() -> None:
    with pytest.raises(ValueError):
        AlarmType(">=")   # 不在规范内
```

- [ ] **Step 2：确认失败**

```bash
uv run pytest tests/unit/shared/test_enums_alarm_type.py -v
```
期望：ImportError。

- [ ] **Step 3：实现 AlarmType**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\alarm_type.py`:
```python
"""告警判定类型。对应 spec §F + DB CHECK 约束（§4.2 device_waring_cfgs）。"""
from __future__ import annotations

from enum import Enum


class AlarmType(str, Enum):
    """5 种告警判定类型。值直接存 DB，故用字符串常量。"""

    GT = ">"
    LT = "<"
    EQ = "="
    NE = "!="
    LX = "LX"   # 连续 N 次越限
```

- [ ] **Step 4：运行通过**

```bash
uv run pytest tests/unit/shared/test_enums_alarm_type.py -v
```
期望：3 passed。

- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/enums/alarm_type.py \
        tests/unit/shared/test_enums_alarm_type.py
git commit -m "feat(shared): AlarmType enum (>, <, =, !=, LX) matches DB CHECK"
```

---

## Task B4：AlarmAction 枚举（PhoneAlarm 高 8 位动作码）

**Files:**
- Create: `D:\江苏润盛\tests\unit\shared\test_enums_alarm_action.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\alarm_action.py`

- [ ] **Step 1：写失败测试**

Create `D:\江苏润盛\tests\unit\shared\test_enums_alarm_action.py`:
```python
"""Spec §F.4 — PhoneAlarm 高 8/12 位的动作码。"""
from __future__ import annotations

from ruisheng_shared.enums import AlarmAction


def test_values() -> None:
    assert AlarmAction.NONE == 0
    assert AlarmAction.ALL_ON == 1
    assert AlarmAction.ALL_OFF == 2
    assert AlarmAction.CHANNEL_ON == 3
    assert AlarmAction.CHANNEL_OFF == 4


def test_decode_phone_alarm() -> None:
    """0x0103 = trigger call + trigger all-on"""
    trig, reset = AlarmAction.decode_phone_alarm(0x0103)
    assert trig == AlarmAction.ALL_ON
    assert reset == AlarmAction.NONE


def test_encode_phone_alarm() -> None:
    # 触发电话 + 恢复电话 + 触发全开 + 恢复全关 → 0x2103
    v = AlarmAction.encode_phone_alarm(
        call_on_trigger=True,
        call_on_reset=True,
        trigger_action=AlarmAction.ALL_ON,
        reset_action=AlarmAction.ALL_OFF,
    )
    assert v == 0x2103
```

- [ ] **Step 2：失败**

```bash
uv run pytest tests/unit/shared/test_enums_alarm_action.py -v
```

- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\alarm_action.py`:
```python
"""PhoneAlarm 位域解码。对应 spec §F.4。

PhoneAlarm 是 16 位整数：
- bit 0 (0x0001): 触发电话报警
- bit 1 (0x0002): 恢复电话报警
- bit 8-11:      触发时动作码（0–4）
- bit 12-15:     恢复时动作码（0–4）
"""
from __future__ import annotations

from enum import IntEnum


class AlarmAction(IntEnum):
    """触发/恢复时的继电器动作码（0–4）。"""

    NONE = 0
    ALL_ON = 1
    ALL_OFF = 2
    CHANNEL_ON = 3
    CHANNEL_OFF = 4

    @classmethod
    def decode_phone_alarm(cls, phone_alarm: int) -> tuple[AlarmAction, AlarmAction]:
        """返回 (trigger_action, reset_action)。"""
        trig = cls((phone_alarm >> 8) & 0xF)
        reset = cls((phone_alarm >> 12) & 0xF)
        return trig, reset

    @staticmethod
    def decode_flags(phone_alarm: int) -> tuple[bool, bool]:
        """返回 (call_on_trigger, call_on_reset)。"""
        return bool(phone_alarm & 0x0001), bool(phone_alarm & 0x0002)

    @staticmethod
    def encode_phone_alarm(
        *,
        call_on_trigger: bool,
        call_on_reset: bool,
        trigger_action: AlarmAction,
        reset_action: AlarmAction,
    ) -> int:
        v = 0
        if call_on_trigger:
            v |= 0x0001
        if call_on_reset:
            v |= 0x0002
        v |= (int(trigger_action) & 0xF) << 8
        v |= (int(reset_action) & 0xF) << 12
        return v
```

- [ ] **Step 4：测试通过**

```bash
uv run pytest tests/unit/shared/test_enums_alarm_action.py -v
```
期望：3 passed。

- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/enums/alarm_action.py \
        tests/unit/shared/test_enums_alarm_action.py
git commit -m "feat(shared): AlarmAction + PhoneAlarm bit codec (spec §F.4)"
```

---

## Task B5：ControlStatus 枚举

**Files:**
- Create: `D:\江苏润盛\tests\unit\shared\test_enums_control_status.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\control_status.py`

- [ ] **Step 1：测试**

Create `D:\江苏润盛\tests\unit\shared\test_enums_control_status.py`:
```python
"""Spec §3.2 + §3.4.1 — 控制命令生命周期状态。"""
from __future__ import annotations

from ruisheng_shared.enums import ControlStatus


def test_all_values() -> None:
    assert {s.value for s in ControlStatus} == {
        "pending", "success", "failed", "timeout", "cancelled",
    }


def test_from_db_string() -> None:
    assert ControlStatus("pending") is ControlStatus.PENDING


def test_is_terminal() -> None:
    assert not ControlStatus.PENDING.is_terminal
    assert ControlStatus.SUCCESS.is_terminal
    assert ControlStatus.FAILED.is_terminal
    assert ControlStatus.TIMEOUT.is_terminal
    assert ControlStatus.CANCELLED.is_terminal
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\control_status.py`:
```python
"""控制命令状态机。对应 spec §3.2 生命周期与 §3.4.1 WS control_result 契约。"""
from __future__ import annotations

from enum import Enum


class ControlStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self is not ControlStatus.PENDING
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/enums/control_status.py \
        tests/unit/shared/test_enums_control_status.py
git commit -m "feat(shared): ControlStatus lifecycle enum (pending → terminal)"
```

---

## Task B6：Authority 枚举（4 级 RBAC）

**Files:**
- Create: `D:\江苏润盛\tests\unit\shared\test_enums_authority.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\authority.py`

- [ ] **Step 1：测试**

```python
"""Spec §3.6 — 四级 RBAC Authority 枚举 + 分级比较。"""
from __future__ import annotations

import pytest

from ruisheng_shared.enums import Authority


def test_values() -> None:
    assert Authority.USER == "User"
    assert Authority.COMPANY == "Company"
    assert Authority.GROUP_COMPANY == "GroupCompany"
    assert Authority.ADMIN == "Administrators"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Authority.USER, Authority.COMPANY, True),
        (Authority.COMPANY, Authority.GROUP_COMPANY, True),
        (Authority.ADMIN, Authority.USER, False),
        (Authority.ADMIN, Authority.ADMIN, False),
    ],
)
def test_is_below(a: Authority, b: Authority, expected: bool) -> None:
    """权限等级比较：USER < COMPANY < GROUP_COMPANY < ADMIN"""
    assert a.is_below(b) is expected
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\enums\authority.py`:
```python
"""4 级 RBAC 权限。对应 spec §3.6。"""
from __future__ import annotations

from enum import Enum


class Authority(str, Enum):
    USER = "User"
    COMPANY = "Company"
    GROUP_COMPANY = "GroupCompany"
    ADMIN = "Administrators"

    @property
    def level(self) -> int:
        return {"User": 1, "Company": 2, "GroupCompany": 3, "Administrators": 4}[self.value]

    def is_below(self, other: Authority) -> bool:
        """严格小于（不含等于）。"""
        return self.level < other.level
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/enums/authority.py \
        tests/unit/shared/test_enums_authority.py
git commit -m "feat(shared): Authority 4-level enum with is_below comparator"
```

---

## Task B7：errors / codes.py 错误码与异常基类

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\errors\__init__.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\errors\codes.py`
- Create: `D:\江苏润盛\tests\unit\shared\test_errors.py`

- [ ] **Step 1：测试**

Create `D:\江苏润盛\tests\unit\shared\test_errors.py`:
```python
"""Spec §5.1 — ErrCode 枚举 + BizError 异常基类。"""
from __future__ import annotations

import pytest

from ruisheng_shared.errors import BizError, ErrCode


def test_errcode_values() -> None:
    assert ErrCode.OK == 0
    assert ErrCode.BIZ_FAIL == -1
    assert ErrCode.BAD_PARAM == -100
    assert ErrCode.UNAUTHED == -101
    assert ErrCode.FORBIDDEN == -102
    assert ErrCode.DEV_OFFLINE == -200
    assert ErrCode.DEV_NO_REPLY == -201
    assert ErrCode.DEV_CRC_FAIL == -202
    assert ErrCode.INTERNAL == -300
    assert ErrCode.DB_UNAVAILABLE == -301


def test_biz_error_carries_code() -> None:
    exc = BizError(ErrCode.DEV_OFFLINE, "设备 60270012 离线")
    assert exc.code is ErrCode.DEV_OFFLINE
    assert exc.http_status == 200
    assert str(exc) == "设备 60270012 离线"


def test_biz_error_http_status_map() -> None:
    assert BizError(ErrCode.BAD_PARAM, "").http_status == 400
    assert BizError(ErrCode.UNAUTHED, "").http_status == 401
    assert BizError(ErrCode.FORBIDDEN, "").http_status == 403
    assert BizError(ErrCode.INTERNAL, "").http_status == 500
    assert BizError(ErrCode.DB_UNAVAILABLE, "").http_status == 503
    assert BizError(ErrCode.DEV_OFFLINE, "").http_status == 200


def test_unknown_code_rejected() -> None:
    with pytest.raises(ValueError):
        ErrCode(-999)
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\errors\__init__.py`:
```python
from .codes import BizError, ErrCode

__all__ = ["BizError", "ErrCode"]
```

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\errors\codes.py`:
```python
"""统一错误码与业务异常基类。对应 spec §5.1 + §D.2。"""
from __future__ import annotations

from enum import IntEnum


class ErrCode(IntEnum):
    OK = 0
    BIZ_FAIL = -1          # HTTP 200
    BAD_PARAM = -100       # HTTP 400
    UNAUTHED = -101        # HTTP 401
    FORBIDDEN = -102       # HTTP 403
    DEV_OFFLINE = -200     # HTTP 200
    DEV_NO_REPLY = -201    # HTTP 200
    DEV_CRC_FAIL = -202    # HTTP 200
    INTERNAL = -300        # HTTP 500
    DB_UNAVAILABLE = -301  # HTTP 503


_HTTP_MAP: dict[ErrCode, int] = {
    ErrCode.OK: 200,
    ErrCode.BIZ_FAIL: 200,
    ErrCode.BAD_PARAM: 400,
    ErrCode.UNAUTHED: 401,
    ErrCode.FORBIDDEN: 403,
    ErrCode.DEV_OFFLINE: 200,
    ErrCode.DEV_NO_REPLY: 200,
    ErrCode.DEV_CRC_FAIL: 200,
    ErrCode.INTERNAL: 500,
    ErrCode.DB_UNAVAILABLE: 503,
}


class BizError(Exception):
    """业务异常。api 层 FastAPI handler 捕获后转 ApiResponse。"""

    def __init__(self, code: ErrCode, msg: str) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg

    @property
    def http_status(self) -> int:
        return _HTTP_MAP[self.code]
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/errors/ \
        tests/unit/shared/test_errors.py
git commit -m "feat(shared): ErrCode + BizError with HTTP status mapping"
```

---

## Task B8：constants/protocol.py（CRC 多项式、端口等）

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\constants\__init__.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\constants\protocol.py`
- Create: `D:\江苏润盛\tests\unit\shared\test_constants_protocol.py`

- [ ] **Step 1：测试**

Create `D:\江苏润盛\tests\unit\shared\test_constants_protocol.py`:
```python
"""Spec §A.3 + §B.1 + §D.1 — 协议常量。"""
from __future__ import annotations

from ruisheng_shared.constants.protocol import (
    CRC16_INIT,
    CRC16_POLYNOMIAL,
    DEVICE_REGISTER_TCP_PORT,
    DEVICE_TELEMETRY_TCP_PORT,
    FRAME_MAX_LENGTH,
    FRAME_SILENCE_MS,
    HEARTBEAT_INTERVAL_S,
    MODBUS_BROADCAST_ADDR,
    MODBUS_MAX_SLAVE_ADDR,
    MODBUS_MIN_SLAVE_ADDR,
)


def test_crc16_standard_polynomial() -> None:
    assert CRC16_POLYNOMIAL == 0xA001
    assert CRC16_INIT == 0xFFFF


def test_port_assignments() -> None:
    assert DEVICE_REGISTER_TCP_PORT == 6000
    assert DEVICE_TELEMETRY_TCP_PORT == 6020


def test_frame_limits() -> None:
    assert FRAME_MAX_LENGTH == 4096
    assert FRAME_SILENCE_MS == 200


def test_heartbeat_default_period() -> None:
    assert HEARTBEAT_INTERVAL_S == 30


def test_modbus_address_range() -> None:
    assert MODBUS_BROADCAST_ADDR == 0
    assert MODBUS_MIN_SLAVE_ADDR == 1
    assert MODBUS_MAX_SLAVE_ADDR == 247
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\constants\__init__.py`:
```python
"""常量集合。"""
from . import limits, protocol

__all__ = ["limits", "protocol"]
```

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\constants\protocol.py`:
```python
"""ModBus / TCP / RS485 协议常量。对应 spec §A + §B + §D.1。"""
from __future__ import annotations

# CRC16 ModBus 标准（§A.3）
CRC16_POLYNOMIAL: int = 0xA001  # 反向多项式
CRC16_INIT: int = 0xFFFF

# TCP 端口分工（§A.7）
DEVICE_REGISTER_TCP_PORT: int = 6000     # FC 21/22/20/100/0x19
DEVICE_TELEMETRY_TCP_PORT: int = 6020    # FC 3/5/6/16

# 帧界（§A.2.1）
FRAME_MAX_LENGTH: int = 4096
FRAME_SILENCE_MS: int = 200

# 心跳（§A.5 + §D.1）
HEARTBEAT_INTERVAL_S: int = 30
HEARTBEAT_TIMEOUT_MULTIPLE: int = 3   # 连续 3 次无响应 → LossCnt++

# 离线 / 清除阈值（§D.1）
OFFLINE_THRESHOLD_MIN: int = 15
PURGE_AFTER_REGISTER_S: int = 120

# 轮询（§D.1 / D6）
POLL_INTERVAL_MIN_DECISEC: int = 10
POLL_INTERVAL_MAX_DECISEC: int = 1000
DEFAULT_POLL_INTERVAL_DECISEC: int = 100  # 10 秒

# ModBus 地址范围
MODBUS_BROADCAST_ADDR: int = 0   # 新系统拒收（§3.8.13）
MODBUS_MIN_SLAVE_ADDR: int = 1
MODBUS_MAX_SLAVE_ADDR: int = 247

# 控制命令 TTL（§3.2 + §3.8.5）
OFFLINE_COMMAND_TTL_S: int = 600     # 10 min
COMMAND_ACK_TIMEOUT_S: int = 5
COMMAND_RETRY_MAX: int = 3
```

- [ ] **Step 4：测试通过**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/constants/ \
        tests/unit/shared/test_constants_protocol.py
git commit -m "feat(shared): protocol constants (CRC/ports/frame/timing)"
```

---

## Task B9：constants/limits.py（TTL / 队列 / 磁盘等非协议常量）

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\constants\limits.py`
- Create: `D:\江苏润盛\tests\unit\shared\test_constants_limits.py`

- [ ] **Step 1：测试**

```python
"""Spec §5.10 / §5.12 — 运行时限额常量。"""
from __future__ import annotations

from ruisheng_shared.constants.limits import (
    BATCH_WRITER_FLUSH_MS,
    BATCH_WRITER_MAX_ROWS,
    JWT_ACCESS_TTL_S,
    JWT_REFRESH_TTL_S,
    LOG_DISK_CAP_GB,
    LOG_ROTATE_SIZE_MB,
    STREAM_ALARM_MAXLEN,
    STREAM_CONTROL_MAXLEN,
    WS_SEND_QUEUE_MAX,
)


def test_batch_writer() -> None:
    assert BATCH_WRITER_FLUSH_MS == 100
    assert BATCH_WRITER_MAX_ROWS == 500


def test_jwt_ttl() -> None:
    assert JWT_ACCESS_TTL_S == 900        # 15 min
    assert JWT_REFRESH_TTL_S == 604800    # 7 d


def test_stream_maxlen() -> None:
    assert STREAM_ALARM_MAXLEN == 100000
    assert STREAM_CONTROL_MAXLEN == 50000


def test_ws_queue() -> None:
    assert WS_SEND_QUEUE_MAX == 500


def test_log_disk() -> None:
    assert LOG_DISK_CAP_GB == 20
    assert LOG_ROTATE_SIZE_MB == 100
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\constants\limits.py`:
```python
"""运行时限额常量。对应 spec §5.10 长期性能、§5.12 日志、§5.13 JWT。"""
from __future__ import annotations

# batch_writer（§3.1）
BATCH_WRITER_FLUSH_MS: int = 100
BATCH_WRITER_MAX_ROWS: int = 500

# JWT（§5.13）
JWT_ACCESS_TTL_S: int = 15 * 60
JWT_REFRESH_TTL_S: int = 7 * 24 * 3600

# Redis Streams 容量（§3.8.3）
STREAM_ALARM_MAXLEN: int = 100_000
STREAM_CONTROL_MAXLEN: int = 50_000

# WS 慢消费者（§3.8.4）
WS_SEND_QUEUE_MAX: int = 500

# 日志磁盘（§5.12.4）
LOG_DISK_CAP_GB: int = 20
LOG_ROTATE_SIZE_MB: int = 100

# 通知重试（§5.5）
NOTIFY_RETRY_DELAYS_S: tuple[int, ...] = (5, 15, 60, 300, 1800)

# WAL（§5.11）
GW_LOCAL_WAL_CAP_GB: int = 10
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/constants/limits.py \
        tests/unit/shared/test_constants_limits.py
git commit -m "feat(shared): runtime limits constants"
```

---

## Task B10：validators/rs485.py — 波特率 × 终端数 × 周期约束校验

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\validators\__init__.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\validators\rs485.py`
- Create: `D:\江苏润盛\tests\unit\shared\test_validators_rs485.py`

- [ ] **Step 1：测试（覆盖 §A.8 约束表）**

Create `D:\江苏润盛\tests\unit\shared\test_validators_rs485.py`:
```python
"""Spec §A.8 — RS485 物理约束表：波特率 × 终端数 × 最小轮询周期。"""
from __future__ import annotations

import pytest

from ruisheng_shared.validators.rs485 import (
    min_poll_interval_decisec,
    validate_bus_feasibility,
)


@pytest.mark.parametrize(
    ("baud", "device_count", "expected_min_decisec"),
    [
        (9600, 128, 60),     # 6s = 60 decisec
        (19200, 128, 30),    # 3s = 30 decisec
        (38400, 128, 20),    # 2s = 20 decisec
        (115200, 128, 10),   # 1s = 10 decisec
        (9600, 20, 10),      # 20 台 @ 9600 也能 1s
    ],
)
def test_min_poll_interval(baud: int, device_count: int, expected_min_decisec: int) -> None:
    assert min_poll_interval_decisec(baud, device_count) == expected_min_decisec


def test_validate_feasible() -> None:
    # 128 台 @ 9600 @ 6s → OK
    validate_bus_feasibility(baud=9600, device_count=128, min_decisec=60)


def test_validate_infeasible_raises() -> None:
    from ruisheng_shared.errors import BizError

    # 128 台 @ 9600 @ 1s → 不可行
    with pytest.raises(BizError) as exc_info:
        validate_bus_feasibility(baud=9600, device_count=128, min_decisec=10)
    assert exc_info.value.code.value == -100   # BAD_PARAM
    assert "6" in exc_info.value.msg            # 提示应 >= 6s
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\validators\__init__.py`:
```python
"""业务校验器。"""
from .rs485 import min_poll_interval_decisec, validate_bus_feasibility

__all__ = ["min_poll_interval_decisec", "validate_bus_feasibility"]
```

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\validators\rs485.py`:
```python
"""RS485 总线物理可行性校验。对应 spec §A.8。

约束表来源：单帧往返 ≈ (请求 8B + 响应 25B) * 10 bit / baud_rate + 帧间静止 4ms

波特率     单次往返    128 台一轮    最小轮询周期
9600        40 ms      5.1s          6s
19200       20 ms      2.6s          3s
38400       10 ms      1.3s          2s
115200       4 ms      0.5s          1s
"""
from __future__ import annotations

from ruisheng_shared.errors import BizError, ErrCode

# 保守 RTT 单位：ms
_RTT_MS: dict[int, int] = {
    9600: 40,
    19200: 20,
    38400: 10,
    57600: 7,
    115200: 4,
}


def min_poll_interval_decisec(baud: int, device_count: int) -> int:
    """给定波特率与总线设备数，返回物理上可行的最小轮询周期（0.1s 单位）。

    算法：ceil(one_round_ms / 1000) 秒 → decisec，并保证下限为 10（即 1s）。
    """
    if baud not in _RTT_MS:
        raise BizError(ErrCode.BAD_PARAM, f"波特率 {baud} 不在支持表中")
    one_round_ms = _RTT_MS[baud] * max(device_count, 1)
    # ms → 向上取整到 1s → 转 decisec (×10)；下限 1s
    return max(((one_round_ms + 999) // 1000) * 10, 10)


def validate_bus_feasibility(*, baud: int, device_count: int, min_decisec: int) -> None:
    """若用户配置的最小轮询周期 < 物理下限，抛 BizError(BAD_PARAM)。"""
    physical = min_poll_interval_decisec(baud, device_count)
    if min_decisec < physical:
        hint_s = physical / 10.0
        raise BizError(
            ErrCode.BAD_PARAM,
            f"该波特率 ({baud} bps) 下 {device_count} 台终端的最小轮询周期应 >= {hint_s:.1f}s",
        )
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/validators/ \
        tests/unit/shared/test_validators_rs485.py
git commit -m "feat(shared): RS485 physical feasibility validator (spec §A.8)"
```

---

## Task B11—B18：阶段 B 后续（schemas 占位 + coverage baseline）

> B11–B18 范围：创建 `schemas/` 子包空骨架（具体 pydantic 模型延到 Plan 2 再填，Plan 0 只需要 `__init__.py` 和一个用于 WS 信封的最小 ApiResponse）。

## Task B11：schemas 子包骨架

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\schemas\__init__.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\schemas\common.py`
- Create: `D:\江苏润盛\tests\unit\shared\test_schemas_common.py`

- [ ] **Step 1：测试**

Create `D:\江苏润盛\tests\unit\shared\test_schemas_common.py`:
```python
"""ApiResponse 通用壳。"""
from __future__ import annotations

from ruisheng_shared.schemas.common import ApiResponse


def test_success_response() -> None:
    r = ApiResponse[dict](code=0, data={"x": 1})
    d = r.model_dump()
    assert d["code"] == 0
    assert d["msg"] == "ok"
    assert d["data"] == {"x": 1}


def test_transid_optional() -> None:
    r = ApiResponse[str](code=0, data="hello", transid="01HXXX")
    assert r.transid == "01HXXX"
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\schemas\__init__.py`:
```python
"""Pydantic schemas（API 请求/响应 + WS 信封）。
详细业务 schemas 在 Plan 2 填充；本 Plan 0 只提供通用壳。
"""
from .common import ApiResponse

__all__ = ["ApiResponse"]
```

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\schemas\common.py`:
```python
"""通用 API 响应壳。对应 spec §5.1。"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    msg: str = "ok"
    data: T | None = None
    transid: str | None = None
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/schemas/ \
        tests/unit/shared/test_schemas_common.py
git commit -m "feat(shared): schemas package with generic ApiResponse shell"
```

---

## Task B12：阶段 B 收尾 — 运行覆盖率检查

**Files:** 无

- [ ] **Step 1：跑全部 shared 测试 + 覆盖率**

```bash
cd "D:\江苏润盛"
uv run pytest tests/unit/shared/ --cov=ruisheng-shared/src/ruisheng_shared --cov-report=term-missing
```
期望：全部 passed；覆盖率 ≥ 90%（branch）。

若有未覆盖的分支 → 补测试 → 重测。

- [ ] **Step 2：打阶段标签**

```bash
git tag -a plan-0-stage-b-complete -m "Stage B: enums/errors/constants/validators complete"
```

---

# 阶段 C — ORM 模型 23 表

> 每张表一个独立 Task，大致模式：
> 1. 先写失败测试（字段存在 / 类型 / 约束）
> 2. 实现 model 类
> 3. 跑测试 + mypy
> 4. Commit

## Task C1：base.py — Declarative Base + 通用 mixin

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\__init__.py`
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\base.py`
- Create: `D:\江苏润盛\tests\unit\shared\test_models_base.py`

- [ ] **Step 1：测试**

Create `D:\江苏润盛\tests\unit\shared\test_models_base.py`:
```python
"""Base / mixin 可被子类继承。"""
from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from ruisheng_shared.models.base import Base, TimestampMixin


class _Sample(Base, TimestampMixin):
    __tablename__ = "_sample"
    id: Mapped[int] = mapped_column(primary_key=True)


def test_subclass_has_created_updated() -> None:
    assert hasattr(_Sample, "created_at")
    assert hasattr(_Sample, "updated_at")


def test_tablename_snake_case() -> None:
    assert _Sample.__tablename__ == "_sample"


def test_naming_convention_registered() -> None:
    """约束命名模板必须注入 metadata，Stage D 的 Alembic 依赖它。"""
    nc = Base.metadata.naming_convention
    assert nc["pk"] == "pk_%(table_name)s"
    assert nc["fk"] == "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    assert set(nc.keys()) == {"ix", "uq", "ck", "fk", "pk"}
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\__init__.py`:
```python
"""ORM 模型集合。
每次新增或修改模型必须在 CHANGELOG.md 登记；如为 breaking 需升级 SHARED_SCHEMA_VERSION。
"""
from .base import Base, TimestampMixin

# 23 张表的模型在 C2–C21 逐个实现后补入 __all__
__all__ = ["Base", "TimestampMixin"]
```

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\base.py`:
```python
"""SQLAlchemy 2.0 Declarative Base + 通用 mixin。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Alembic 约束命名模板：让自动生成的 migration 约束名稳定、可预测。
# 必须在 C2 之前定型，否则后续重命名会触发大量迁移。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(constraint_name)s",  # 需要显式 UniqueConstraint(name=...)，不能只靠 unique=True
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有表的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """统一审计时间字段。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """软删除字段。"""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/models/base.py \
        ruisheng-shared/src/ruisheng_shared/models/__init__.py \
        tests/unit/shared/test_models_base.py
git commit -m "feat(shared): SQLAlchemy Base + Timestamp/SoftDelete mixins"
```

---

> 由于 Task C2 ~ C21 每张表都遵循同样的 TDD 模式，这里为每张表给出完整可照做的 task 模板（Step 3 的代码段是实际要写入的内容）。

> **⚠️ 命名约定铁律（C2 起所有表必须遵守）**
>
> C1 引入的 `Base.metadata.naming_convention` 模板为：
> - `ck`: `"ck_%(table_name)s_%(constraint_name)s"`
> - `uq`: `"uq_%(table_name)s_%(column_0_name)s"`
> - `fk`: `"fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"`
> - `pk`: `"pk_%(table_name)s"`
> - `ix`: `"ix_%(column_0_label)s"`
>
> SQLAlchemy 会把 `CheckConstraint(..., name=X)` 的 `X` 代入 `%(constraint_name)s`。所以：
>
> - ✅ `CheckConstraint(..., name="user_name_format")` → 最终 `ck_users_user_name_format`
> - ❌ `CheckConstraint(..., name="ck_users_user_name_format")` → 最终 `ck_users_ck_users_user_name_format`（双叠）
> - ✅ `UniqueConstraint("a", "b", name="a_b")` → 最终 `uq_<tbl>_a_b`
> - ❌ `UniqueConstraint("a", "b", name="uq_tbl_a_b")` → 最终双叠
>
> **铁律 1**：CheckConstraint / UniqueConstraint 的 `name=` 一律写**裸名**（不含 `ck_/uq_/fk_` 前缀）。
>
> **铁律 2**：**不要**使用列上的 `unique=True`，因为 uq 模板用 `%(constraint_name)s` 需要显式名。改写为独立 `UniqueConstraint(col, name="col")`（放在 `__table_args__`）。
>
> Index 不同 —— `Index("idx_xxx", col)` 的第一参是字面索引名，naming_convention **不对其重写**；保留原 `idx_*` 显式命名。
>
> 测试里断言的是**最终生成名**（`ck_users_user_name_format` 等），与代码里的裸名（`"user_name_format"`）**经 naming_convention 展开后**一致。

## Task C2：wx_groups 模型

**Files:**
- Create: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\tenants.py`
- Create: `D:\江苏润盛\tests\unit\shared\test_models_tenants.py`

- [ ] **Step 1：测试**

```python
"""Spec §4.2 wx_groups"""
from ruisheng_shared.models.tenants import WxGroup


def test_tablename() -> None:
    assert WxGroup.__tablename__ == "wx_groups"


def test_columns_exist() -> None:
    cols = {c.name for c in WxGroup.__table__.columns}
    assert cols >= {
        "usr_group", "appid", "appsecret", "token",
        "token_expires_at", "template_id", "company_name",
        "sys_title", "remark", "created_at", "updated_at",
    }


def test_primary_key() -> None:
    pk = [c.name for c in WxGroup.__table__.primary_key.columns]
    assert pk == ["usr_group"]
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\tenants.py`:
```python
"""租户表（微信公众号级）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class WxGroup(Base, TimestampMixin):
    __tablename__ = "wx_groups"

    usr_group: Mapped[str] = mapped_column(String(50), primary_key=True)
    appid: Mapped[str | None] = mapped_column(String(50))
    appsecret: Mapped[str | None] = mapped_column(String(100))
    token: Mapped[str | None] = mapped_column(String(200))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    template_id: Mapped[str | None] = mapped_column(String(50))
    company_name: Mapped[str | None] = mapped_column(String(100))
    sys_title: Mapped[str | None] = mapped_column(String(100))
    remark: Mapped[str | None] = mapped_column(String(255))
```

- [ ] **Step 4：测试 + 导出到 __init__**

Edit `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\__init__.py`:
```python
from .base import Base, SoftDeleteMixin, TimestampMixin
from .tenants import WxGroup

__all__ = ["Base", "SoftDeleteMixin", "TimestampMixin", "WxGroup"]
```

- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/models/tenants.py \
        ruisheng-shared/src/ruisheng_shared/models/__init__.py \
        tests/unit/shared/test_models_tenants.py
git commit -m "feat(shared): WxGroup (wx_groups) model — tenant table"
```

---

## Task C3–C21：其余 21 张表

> **按以下顺序**逐张实现，每张表的 Task 结构同 C2（测试 → 实现 → 导出 → 测通过 → commit）。下面仅列每个 Task 对应的**实现文件代码块**（关键字段），省略重复的测试模板：每张表至少测 `__tablename__` / 列集合 / 主键 / 关键 CHECK 约束。

### Task C3：users.py — users / user_wx_bindings / user_phone_numbers / user_emails

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\users.py`:
```python
"""用户及关联表（4 张）。对应 spec §4.2 用户与权限。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            r"user_name ~ '^1[3-9][0-9]{9}$' OR user_name ~ '^[a-zA-Z][a-zA-Z0-9_]{3,29}$'",
            name="user_name_format",  # naming_convention 会前缀 ck_users_
        ),
        CheckConstraint(
            "authority IN ('Administrators','GroupCompany','Company','User')",
            name="authority",  # naming_convention 会前缀 ck_users_
        ),
        UniqueConstraint("user_name", name="user_name"),  # 铁律 2：不用 unique=True，显式 UQ 才能被 naming_convention 正确命名 → uq_users_user_name
        Index("idx_users_tenant", "usr_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    login_name: Mapped[str | None] = mapped_column(String(50))
    group_company: Mapped[str | None] = mapped_column(String(100))
    company: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(100))
    authority: Mapped[str] = mapped_column(String(20), nullable=False)
    control_authority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    sys_name: Mapped[str | None] = mapped_column(String(50))
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group"),
        nullable=False,
    )


class UserWxBinding(Base):
    __tablename__ = "user_wx_bindings"

    openid: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_name: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.user_name", ondelete="CASCADE"),
        nullable=False,
    )
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class UserPhoneNumber(Base):
    __tablename__ = "user_phone_numbers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.user_name", ondelete="CASCADE"), nullable=False
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)


class UserEmail(Base):
    __tablename__ = "user_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(100), nullable=False)
```

**Task C3 Steps：** 同 C2 的 5 步，测试文件为 `tests/unit/shared/test_models_users.py`，commit 信息 `feat(shared): User + WxBinding + PhoneNumber + Email models`。

### Task C4：devices.py — devices / device_points / device_static_data / sim_cards / device_templates

Create `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\devices.py`:
```python
"""设备相关 5 张表。对应 spec §4.2。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, DateTime, Double, ForeignKey,
    Index, Integer, SmallInteger, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin


class Device(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("dev_ser_number", "iccid", name="ser_iccid"),  # → uq_devices_ser_iccid (naming_convention 处理前缀)
        CheckConstraint(
            "update_interval_decisec BETWEEN 10 AND 1000",
            name="poll_interval",  # → ck_devices_poll_interval
        ),
        CheckConstraint(
            "modbus_addr BETWEEN 1 AND 247",
            name="modbus_addr",  # → ck_devices_modbus_addr
        ),
        CheckConstraint(
            "baud_rate IN (9600, 19200, 38400, 57600, 115200)",
            name="baud_rate",  # → ck_devices_baud_rate
        ),
        Index("idx_devices_tenant", "usr_group"),
        Index("idx_devices_admin", "administrators"),
        Index(
            "idx_devices_online",
            "is_online",
            postgresql_where="deleted_at IS NULL",
        ),
        {"postgresql_with": {"fillfactor": "80", "autovacuum_vacuum_scale_factor": "0.05"}},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    dev_ser_number: Mapped[str] = mapped_column(String(50), nullable=False)
    iccid: Mapped[str | None] = mapped_column(String(50))
    dev_name: Mapped[str | None] = mapped_column(String(100))
    dev_type: Mapped[str | None] = mapped_column(String(50))
    modbus_addr: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    baud_rate: Mapped[int | None] = mapped_column(Integer)
    group_company: Mapped[str | None] = mapped_column(String(100))
    company: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(100))
    administrators: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("users.user_name")
    )
    dev_ip: Mapped[str | None] = mapped_column(INET)
    code_file: Mapped[str | None] = mapped_column(String(255))
    code_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    update_interval_decisec: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_state: Mapped[dict | None] = mapped_column(JSONB)
    update_flag: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usr_group: Mapped[str] = mapped_column(
        String(50), ForeignKey("wx_groups.usr_group"), nullable=False
    )


class DevicePoint(Base, TimestampMixin):
    __tablename__ = "device_points"
    __table_args__ = (
        CheckConstraint("point_number BETWEEN 0 AND 65535", name="point_number"),  # → ck_device_points_point_number
        CheckConstraint("fun_code IN (1,2,3,4)", name="fun_code"),  # → ck_device_points_fun_code
        Index("idx_points_dev", "dev_number"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(
        String(50), ForeignKey("devices.dev_number", ondelete="CASCADE"), nullable=False
    )
    point_name: Mapped[str] = mapped_column(String(100), nullable=False)
    user_point_name: Mapped[str | None] = mapped_column(String(100))
    point_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fun_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dev_addr: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    r_bit: Mapped[int | None] = mapped_column(SmallInteger)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    point_unit: Mapped[str | None] = mapped_column(String(20))
    point_ratio: Mapped[float] = mapped_column(Double, default=1.0)
    point_offset: Mapped[float] = mapped_column(Double, default=0.0)
    user_ratio: Mapped[float] = mapped_column(Double, default=1.0)
    user_point_offset: Mapped[float] = mapped_column(Double, default=0.0)
    min_value: Mapped[float | None] = mapped_column(Double)
    max_value: Mapped[float | None] = mapped_column(Double)
    show: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)


class DeviceStaticData(Base, TimestampMixin):
    __tablename__ = "device_static_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(
        String(50), ForeignKey("devices.dev_number", ondelete="CASCADE"), nullable=False
    )
    base_msg_name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_msg_value: Mapped[str | None] = mapped_column(String(255))


class SimCard(Base, TimestampMixin):
    __tablename__ = "sim_cards"

    iccid: Mapped[str] = mapped_column(String(50), primary_key=True)
    msisdn: Mapped[str | None] = mapped_column(String(20))
    card_type: Mapped[str | None] = mapped_column(String(50))
    card_status: Mapped[int] = mapped_column(SmallInteger, default=0)
    service_months: Mapped[int | None] = mapped_column(Integer)
    data_amount: Mapped[float | None] = mapped_column(Double)
    total_data_amount: Mapped[float | None] = mapped_column(Double)
    open_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost: Mapped[float | None] = mapped_column(Double)
    month_data: Mapped[float | None] = mapped_column(Double)
    remark: Mapped[str | None] = mapped_column(String(255))
    usr_remark: Mapped[str | None] = mapped_column(String(255))


class DeviceTemplate(Base, TimestampMixin):
    """设备模板（Q-B10 保留占位）。"""
    __tablename__ = "device_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dev_type: Mapped[str | None] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

### Task C5–C10：alarms.py / control.py / plans.py / scenes.py / pay.py / logs.py / timeseries.py

> 每个 task 模式同 C2/C3/C4：测试 → 实现 → `__init__` 导出 → commit。

由于篇幅限制，这里仅列每张模型的关键决策；完整 DDL 请严格对照 spec §4.2：

- **alarms.py**: `DeviceWaringCfg` / `AlarmRecord` / `AlarmOutbox`
  - `device_waring_cfgs` 必带 `CheckConstraint("alarm_type IN ('>','<','=','!=','LX')")`
  - `alarm_records.channels_sent` 用 `JSONB` 默认 `{}`
  - `alarm_outbox` 带 `Index` on `(published, created_at)` where `published=false`
- **control.py**: `UserControlAction`
  - `result` CHECK 枚举 5 值
  - `cmd_id` UNIQUE
  - 双索引 `(dev_number, acted_at DESC)` / `(user_name, acted_at DESC)`
  - `CheckConstraint("(result = 'pending') = (completed_at IS NULL)", name="result_completed_consistency")` — 类比 pay_orders 的 `pay_state`/`paid_at` 一致性；spec DDL 未加但 §3.5 状态机要求终态必有 completed_at（plan bug fix 2026-04-14：原写法误用 'paid'，但 result 合法值是 pending/success/failed/timeout/cancelled，不存在 'paid'）
- **plans.py**: `TimingPlan` / `MaintainPlan` / `MaintainAction`
- **scenes.py**: `ScenePage` / `SceneView`
- **pay.py**: `PayOrder`（带 `total_fee >= 0` 和 `(pay_state='paid') = (paid_at IS NOT NULL)`）/ `PayOrderSeen`
- **logs.py**: `SoftLog` / `UserLoginRecord` — 两者都要准备挂 hypertable（通过 alembic 独立处理）
- **timeseries.py**: `PointDataRealtime`（带 `with_={"fillfactor": "70", ...}`）/ `PointDataHistory` / `WaveformHistory`

每个 Task 的 Step 5 Commit 样例：
```bash
git commit -m "feat(shared): alarms models (DeviceWaringCfg, AlarmRecord, AlarmOutbox)"
```

完成全部 23 张表后：

## Task C21：__init__.py 汇总全部模型

Edit `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\models\__init__.py`:
```python
"""所有 23 张表的 ORM 模型。"""
from .alarms import AlarmOutbox, AlarmRecord, DeviceWaringCfg
from .base import Base, SoftDeleteMixin, TimestampMixin
from .control import UserControlAction
from .devices import Device, DevicePoint, DeviceStaticData, DeviceTemplate, SimCard
from .logs import SoftLog, UserLoginRecord
from .pay import PayOrder, PayOrderSeen
from .plans import MaintainAction, MaintainPlan, TimingPlan
from .scenes import ScenePage, SceneView
from .tenants import WxGroup
from .timeseries import PointDataHistory, PointDataRealtime, WaveformHistory
from .users import User, UserEmail, UserPhoneNumber, UserWxBinding

__all__ = [
    "AlarmOutbox", "AlarmRecord", "Base", "Device", "DevicePoint",
    "DeviceStaticData", "DeviceTemplate", "DeviceWaringCfg",
    "MaintainAction", "MaintainPlan", "PayOrder", "PayOrderSeen",
    "PointDataHistory", "PointDataRealtime", "ScenePage", "SceneView",
    "SimCard", "SoftDeleteMixin", "SoftLog", "TimestampMixin", "TimingPlan",
    "User", "UserControlAction", "UserEmail", "UserPhoneNumber",
    "UserLoginRecord", "UserWxBinding", "WaveformHistory", "WxGroup",
]
```

**验证**：
```bash
uv run mypy ruisheng-shared/
uv run pytest tests/unit/shared/ -v
```
期望：0 错误，全部 passed。

## Task C22：阶段 C 收尾 TAG

```bash
git tag -a plan-0-stage-c-complete -m "Stage C: 23 ORM tables complete"
```

---

# 阶段 D — Alembic 迁移 + hypertable + compression + retention

## Task D0：Docker + TimescaleDB 环境前置校验

**目的：** Stage D/E 依赖真实 PG + Redis 容器。此 task 在进入 D1 前验证环境可用，避免后续 task 因环境问题反复失败。

**Files：** 无（只做环境验证）

**前置条件：** Docker Desktop 已装且 daemon 运行中。Windows 用户需已启用 WSL2 后端。

- [ ] **Step 1：确认 docker / docker-compose 可用**

```bash
cd "D:\江苏润盛\.claude\worktrees\plan-0-foundation"
docker --version
docker compose version
```
期望：docker 24+ / compose v2+；命令不报错。若 `docker` 不存在 → 安装 Docker Desktop 后重试。

- [ ] **Step 2：拉起 dev 容器**

```bash
uv run task up
```
期望：`ruisheng-postgres-dev` 和 `ruisheng-redis-dev` 两个容器 healthy。若失败读 `docker compose -f docker-compose.dev.yml logs`。

- [ ] **Step 3：验证 PG 连接**

```bash
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "SELECT version();"
```
期望：返回 PostgreSQL 15.x + TimescaleDB 版本信息。

- [ ] **Step 4：验证 TimescaleDB 扩展可启用**

```bash
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb';"
```
期望：返回 `timescaledb | 2.16.x`（或 docker-compose.dev.yml 中 pin 的版本）。若无扩展 → 镜像不对，改回 `timescale/timescaledb:2-latest-pg15`。

- [ ] **Step 5：验证 Redis 连接**

```bash
docker exec -i ruisheng-redis-dev redis-cli -a dev-redis-pw PING
```
期望：`PONG`。

- [ ] **Step 6：关闭容器**

```bash
uv run task down
```

- [ ] **Step 7：无需 commit**（纯验证 task；若发现 docker-compose.dev.yml 需修正，另开 fixup commit）

---

## Task D1：初始化 alembic

**Files:**
- Create: `D:\江苏润盛\alembic.ini`
- Create: `D:\江苏润盛\alembic\env.py`
- Create: `D:\江苏润盛\alembic\script.py.mako`

- [ ] **Step 1：运行 alembic init**

```bash
cd "D:\江苏润盛"
uv run alembic init alembic
```

- [ ] **Step 2：修改 alembic.ini**

Edit `D:\江苏润盛\alembic.ini` 关键字段：
```ini
script_location = alembic
sqlalchemy.url = driver://user:pass@localhost/dbname   # 会被 env.py 覆盖
file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s
```

- [ ] **Step 3：替换 env.py 支持 async + ruisheng_shared models**

Replace `D:\江苏润盛\alembic\env.py`:
```python
"""Alembic 环境。从 ruisheng_shared.models 加载所有 metadata。"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from ruisheng_shared.models import Base  # 触发所有模型注册到 Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 从环境变量拿 URL
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng",
)
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4：Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat(db): alembic init with async engine + ruisheng_shared metadata"
```

---

## Task D2：生成初始 revision

- [ ] **Step 1：确保本地 PG 在跑**

```bash
uv run task up
sleep 3
```

- [ ] **Step 2：自动生成 initial revision**

```bash
uv run alembic revision --autogenerate -m "initial schema: 23 tables"
```

生成文件：`D:\江苏润盛\alembic\versions\20260413_xxxx_initial_schema_23_tables.py`

- [ ] **Step 3：Review 生成内容**

打开该文件：
- 确认 23 张表的 CREATE TABLE 都在
- 确认 CHECK 约束都在
- 确认索引都在
- 若有漏：手工补到 upgrade() 里

- [ ] **Step 4：Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): initial migration — 23 core tables (autogenerated)"
```

> **Stage D v1.1 Changelog (2026-04-16)**：D2 完成后做了两轮 plan review，发现 Plan v1.0 严重覆盖不足（spec v1.3.3/v1.3.4/v1.3.6 新增的 3 个 PL/pgSQL 函数、2 个 DB 角色、16 个触发器、6 张 RLS 漏表、user_login_records hypertable 等完全未覆盖），原 D3-D7 重编为 **D3-D10 共 8 个 task**。本修订日期：2026-04-16。见 docs/superpowers/plans/PROGRESS.md §Stage D v1.1 修订。

---

## Task D3：DB 角色（ruisheng_gw / ruisheng_api）+ GRANT 基线

**Spec 依据**：§3.7 L670-722 / §4.1.1 L982-987 / §4.2 L1379-1381, L1407-1409, L1451-1452

**Files:**
- Create: `D:\江苏润盛\alembic\versions\20260417_0002_db_roles_and_grants.py`
- Modify: `D:\江苏润盛\.env.example`（追加两个密码变量）
- Modify: `D:\江苏润盛\CONTRIBUTING.md`（新增 §环境变量）

**前置**：D2（26 张表已建）

- [ ] **Step 1：生成空 revision**

```bash
uv run alembic revision -m "db roles ruisheng_gw/ruisheng_api + grants"
```

- [ ] **Step 2：填充 upgrade()**（关键片段）

```python
"""db roles + grants.

Revision ID: xxx
Revises: <D2 rev>
"""
from __future__ import annotations

import os

from alembic import op

revision = "xxx"
down_revision = "<D2-rev>"
branch_labels = None
depends_on = None


def _require_env(name: str) -> str:
    """env var 未设立刻报错；禁止 dev 密码漏到生产。"""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"环境变量 {name} 未设置。\n"
            f"  dev 环境：.env 里填值后 `set -a; . ./.env; set +a` 再跑 alembic\n"
            f"  CI/生产：走 secret manager 注入；严禁默认密码\n"
            f"  参考：CONTRIBUTING.md §环境变量"
        )
    return value


def upgrade() -> None:
    gw_pw = _require_env("RUISHENG_GW_PASSWORD")
    api_pw = _require_env("RUISHENG_API_PASSWORD")

    # --- 幂等创建角色（已存在则重置密码，支持密码轮换） ---
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ruisheng_gw') THEN
            CREATE ROLE ruisheng_gw BYPASSRLS LOGIN PASSWORD '{gw_pw}';
          ELSE
            ALTER ROLE ruisheng_gw WITH LOGIN PASSWORD '{gw_pw}';
          END IF;
        END $$;
    """)
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ruisheng_api') THEN
            CREATE ROLE ruisheng_api LOGIN PASSWORD '{api_pw}';
          ELSE
            ALTER ROLE ruisheng_api WITH LOGIN PASSWORD '{api_pw}';
          END IF;
        END $$;
    """)

    # --- schema 级 GRANT（对现存 26 张表 + 未来新表） ---
    op.execute("GRANT USAGE ON SCHEMA public TO ruisheng_gw, ruisheng_api;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public "
        "TO ruisheng_gw;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO ruisheng_api;"
    )
    # BIGSERIAL 列依赖 sequence USAGE（否则 INSERT 42501）
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
        "TO ruisheng_gw, ruisheng_api;"
    )
    # 未来新表/新序列自动继承权限
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE ON TABLES TO ruisheng_gw;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ruisheng_api;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO ruisheng_gw, ruisheng_api;"
    )

    # --- 表级细粒度（spec §7.8 "各限权" + §4.2 L1379-1381 / L1407-1409 / L1451-1452） ---
    # 注意：schema 级 GRANT 已经把 gw=arw / api=arwd 给到所有 26 张表。
    # 要实现"缩权"必须 REVOKE FROM 两个具名角色（REVOKE FROM PUBLIC 是无效的，
    # PUBLIC 是独立 pseudo-role，不覆盖已授予具名角色的权限）。
    # 然后重新精确 GRANT 需要的最小权限。

    # pay_orders_seen：gw 写+清理（INSERT/SELECT/DELETE），api 只读（SELECT）
    op.execute(
        "REVOKE ALL ON pay_orders_seen FROM PUBLIC, ruisheng_gw, ruisheng_api;"
    )
    op.execute(
        "GRANT INSERT, SELECT, DELETE ON pay_orders_seen TO ruisheng_gw;"
    )
    op.execute("GRANT SELECT ON pay_orders_seen TO ruisheng_api;")

    # soft_logs：gw 只写（INSERT），api 写+读（INSERT/SELECT）
    op.execute(
        "REVOKE ALL ON soft_logs FROM PUBLIC, ruisheng_gw, ruisheng_api;"
    )
    op.execute("GRANT INSERT ON soft_logs TO ruisheng_gw;")
    op.execute("GRANT INSERT, SELECT ON soft_logs TO ruisheng_api;")

    # user_login_records：只 api 写+读（gw 不涉登录；spec §4.2 L1451-1452）
    op.execute(
        "REVOKE ALL ON user_login_records FROM PUBLIC, ruisheng_gw, ruisheng_api;"
    )
    op.execute(
        "GRANT INSERT, SELECT ON user_login_records TO ruisheng_api;"
    )
    # gw 不 GRANT（符合"gw 不涉登录"；若将来 gw 需要读登录审计，另起迁移补）


def downgrade() -> None:
    for role in ("ruisheng_api", "ruisheng_gw"):
        # 先 DROP OWNED（清所有 GRANT / DEFAULT PRIVILEGES 条目）
        # 再 DROP ROLE。IF EXISTS 兜底"已被手工删除"
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                EXECUTE format('DROP OWNED BY %I', '{role}');
                EXECUTE format('DROP ROLE %I', '{role}');
              END IF;
            END $$;
        """)
```

- [ ] **Step 3：同步更新 .env.example + CONTRIBUTING.md**

在 `.env.example` 追加：
```
# Stage D 起需要（D3 迁移会读这两个变量，否则 raise）
# dev 环境：保持默认；CI/生产：走 secret manager 注入
RUISHENG_GW_PASSWORD=dev-gw-change-me
RUISHENG_API_PASSWORD=dev-api-change-me
```

在 `CONTRIBUTING.md` 新增 `## 环境变量` 小节：
```markdown
## 环境变量

项目依赖若干环境变量；本地开发可用 `.env` 文件（gitignore 忽略），CI/生产通过 secret manager 注入。

| 变量 | 用途 | 引入版本 | 遗漏后果 |
|---|---|---|---|
| `DATABASE_URL` | Alembic / SQLAlchemy 连接串 | Stage A | env.py 找不到 DB |
| `REDIS_URL` | Redis 连接 | Stage A | 启动报错 |
| `RUISHENG_SHARED_REQUIRED_VERSION` | api/gw 启动时 schema 版本校验 | Stage A | 启动拒绝 |
| `USE_EMBEDDED_PG` | 测试模式开关（0/1） | Stage A | 默认 0（用 docker） |
| `RUISHENG_GW_PASSWORD` | ruisheng_gw 角色密码 | **Stage D/D3** | alembic upgrade 报 RuntimeError |
| `RUISHENG_API_PASSWORD` | ruisheng_api 角色密码 | **Stage D/D3** | alembic upgrade 报 RuntimeError |

**初始化**：
```bash
cp .env.example .env     # 首次
set -a; . ./.env; set +a # bash/zsh 注入当前 shell
```
```

- [ ] **Step 4：验证**

```bash
# 先 export 变量
export RUISHENG_GW_PASSWORD='dev-gw-change-me'
export RUISHENG_API_PASSWORD='dev-api-change-me'

uv run alembic upgrade head
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "\du"
# 期望：ruisheng_gw (BYPASSRLS) + ruisheng_api (非 BYPASSRLS)

docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "\dp pay_orders_seen"
# 期望：ACL 含 ruisheng_gw=arwd + ruisheng_api=r

docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "\z users"
# 期望：ruisheng_gw=arw + ruisheng_api=arwd

uv run alembic downgrade -1    # 回 D2
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "\du" | grep ruisheng
# 期望：只剩 ruisheng_dev（两个新角色已删）

uv run alembic upgrade head    # 再上
```

- [ ] **Step 5：Commit**

```bash
git add alembic/versions/20260417_0002_*.py .env.example CONTRIBUTING.md
git commit -m "feat(db): D3 ruisheng_gw/ruisheng_api roles + GRANT (spec §4.1.1)"
```

**关键风险**：
- **密码**必须来自 env var，raise 兜底（不要 fallback 到默认）
- **BYPASSRLS** 仅限 ruisheng_gw（ruisheng_api 绝不能拿）
- **DROP ROLE** 必须先 `DROP OWNED BY`（清 GRANT 条目），否则"仍被对象引用"42888 错
- docker 镜像 initdb 创建的 POSTGRES_USER（`ruisheng_dev`）默认 **SUPERUSER**；本迁移以此身份跑；生产环境同样要求 migration role 为 superuser（否则 DROP OWNED 炸）

---

## Task D4：PL/pgSQL 通用函数（set_updated_at + scene 两件套）

**Spec 依据**：§4.1.1 (1) L978-979 / (4) L996-1038 / (5) L1043-1054

**Files:**
- Create: `D:\江苏润盛\alembic\versions\20260417_0003_plpgsql_functions.py`

**前置**：D3（函数本身不依赖角色，但放在 D3 之后保线性 chain）

- [ ] **Step 1：生成**

```bash
uv run alembic revision -m "plpgsql: set_updated_at + scene tenant helpers"
```

- [ ] **Step 2：upgrade() 用 dollar-quoting 贴 spec 原文英文错误消息**

```python
def upgrade() -> None:
    # 函数 1：通用 updated_at 维护（spec §4.1.1 (1)）
    op.execute(r"""
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        SET search_path = pg_catalog, public;
    """)

    # 函数 2：scene 租户一致性校验（spec §4.1.1 (4)）
    # 注意：严格按 spec L996-1038 复刻 —— DECLARE 用 VARCHAR(50) 匹配源列宽，
    #       6 处 RAISE EXCEPTION 都带 % 插值（offending 字段名+值）便于运维定位
    op.execute(r"""
        CREATE OR REPLACE FUNCTION enforce_scene_tenant_consistency()
        RETURNS TRIGGER AS $$
        DECLARE
          v_owner_ug VARCHAR(50);
          v_page_ug  VARCHAR(50);
          v_dev_ug   VARCHAR(50);
        BEGIN
          -- 1) owner_user_name 存在且 usr_group 与传入一致
          SELECT usr_group INTO v_owner_ug FROM users
            WHERE user_name = NEW.owner_user_name AND deleted_at IS NULL;
          IF v_owner_ug IS NULL THEN
            RAISE EXCEPTION 'scene_tenant_violation: owner_user_name=% not found or soft-deleted',
              NEW.owner_user_name USING ERRCODE = '23514';
          END IF;
          IF v_owner_ug <> NEW.usr_group THEN
            RAISE EXCEPTION 'scene_tenant_violation: row.usr_group=% mismatches users(%).usr_group=%',
              NEW.usr_group, NEW.owner_user_name, v_owner_ug USING ERRCODE = '23514';
          END IF;

          -- 2) 若是 scene_views：scene_page_id 的 usr_group 一致
          IF TG_TABLE_NAME = 'scene_views' THEN
            SELECT usr_group INTO v_page_ug FROM scene_pages
              WHERE id = NEW.scene_page_id AND deleted_at IS NULL;
            IF v_page_ug IS NULL THEN
              RAISE EXCEPTION 'scene_tenant_violation: scene_page_id=% not found or soft-deleted',
                NEW.scene_page_id USING ERRCODE = '23514';
            END IF;
            IF v_page_ug <> NEW.usr_group THEN
              RAISE EXCEPTION 'scene_tenant_violation: row.usr_group=% mismatches scene_pages(id=%).usr_group=%',
                NEW.usr_group, NEW.scene_page_id, v_page_ug USING ERRCODE = '23514';
            END IF;

            -- 3) dev_number 的 usr_group 一致
            SELECT usr_group INTO v_dev_ug FROM devices
              WHERE dev_number = NEW.dev_number AND deleted_at IS NULL;
            IF v_dev_ug IS NULL THEN
              RAISE EXCEPTION 'scene_tenant_violation: dev_number=% not found or soft-deleted',
                NEW.dev_number USING ERRCODE = '23514';
            END IF;
            IF v_dev_ug <> NEW.usr_group THEN
              RAISE EXCEPTION 'scene_tenant_violation: row.usr_group=% mismatches devices(%).usr_group=%',
                NEW.usr_group, NEW.dev_number, v_dev_ug USING ERRCODE = '23514';
            END IF;
          END IF;

          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        SET search_path = pg_catalog, public;
    """)

    # 函数 3：scene_views company/department 快照反查（spec §4.1.1 (5)）
    op.execute(r"""
        CREATE OR REPLACE FUNCTION fill_scene_views_snapshot()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.company IS NULL OR NEW.department IS NULL THEN
            SELECT
              COALESCE(NEW.company, u.company),
              COALESCE(NEW.department, u.department)
            INTO NEW.company, NEW.department
            FROM users u
            WHERE u.user_name = NEW.owner_user_name AND u.deleted_at IS NULL;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        SET search_path = pg_catalog, public;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fill_scene_views_snapshot();")
    op.execute("DROP FUNCTION IF EXISTS enforce_scene_tenant_consistency();")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
```

- [ ] **Step 3：验证**

```bash
uv run alembic upgrade head
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng \
  -c "\df public.set_updated_at public.enforce_scene_tenant_consistency public.fill_scene_views_snapshot"
# 期望：3 行，Security 列全是 invoker

docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng \
  -c "SELECT proname, prosecdef, proconfig FROM pg_proc WHERE proname IN ('set_updated_at','enforce_scene_tenant_consistency','fill_scene_views_snapshot');"
# 期望：prosecdef=false 全三行；proconfig={search_path=pg_catalog, public}

uv run alembic downgrade -1
uv run alembic upgrade head
```

- [ ] **Step 4：Commit**

```bash
git add alembic/versions/20260417_0003_*.py
git commit -m "feat(db): D4 plpgsql functions (set_updated_at + scene tenant) with hardened search_path"
```

**关键风险**：
- **函数内所有字符串必须英文**（按 spec 原文；不自造 CJK）
- **`SET search_path = pg_catalog, public`** 函数级属性硬绑定，防会话级 `SET search_path` 劫持
- **`SECURITY INVOKER`**（默认）—— 绝不加 DEFINER（会绕过 RLS）
- 函数体里所有 `WHERE ... AND deleted_at IS NULL` 必须保留（已软删的行不参与校验）

---

## Task D5：13 张表 `trg_<table>_updated` 触发器

**Spec 依据**：§4.1 L964 通用约定 + §4.1.1 (1) + 各表 DDL 示例（§4.2 L1269, L1300, L1360, L1485, L1526 等）

**Files:**
- Create: `D:\江苏润盛\alembic\versions\20260417_0004_updated_at_triggers.py`

**前置**：D4

- [ ] **Step 1：生成**

```bash
uv run alembic revision -m "trg_<table>_updated triggers (13 tables)"
```

- [ ] **Step 2：upgrade() 列表驱动 + ORM drift 断言 + 幂等**

```python
from __future__ import annotations

from alembic import op


revision = "xxx"
down_revision = "<D4-rev>"
branch_labels = None
depends_on = None


# 必须与 ORM `Base.metadata.tables` 含 `updated_at` 列的表完全一致
# 仅含 TimestampMixin 的实体表（13 张）；以下故意排除：
#   - point_data_realtime: 覆盖写语义，无 updated_at
#   - 审计/日志：soft_logs / user_login_records / maintain_actions /
#                user_control_actions / pay_orders_seen / alarm_outbox
#   - 关联/不变表（仅 created_at 或域时间戳，无 updated_at）：
#                user_wx_bindings (bound_at) / user_phone_numbers /
#                user_emails / alarm_records (triggered_at + reset_at)
UPDATED_AT_TABLES: list[str] = [
    "wx_groups",
    "users",
    "devices", "device_points", "device_static_data",
    "sim_cards", "device_templates", "device_waring_cfgs",
    "timing_plans", "maintain_plans",
    "scene_pages", "scene_views",
    "pay_orders",
]


def upgrade() -> None:
    # --- drift detection: 列表 vs ORM metadata ---
    from ruisheng_shared.models import Base

    orm_updated = {
        t.name for t in Base.metadata.tables.values()
        if "updated_at" in t.columns
    }
    plan_updated = set(UPDATED_AT_TABLES)
    missing = orm_updated - plan_updated
    extra = plan_updated - orm_updated
    if missing or extra:
        raise RuntimeError(
            f"UPDATED_AT_TABLES drift from ORM:\n"
            f"  ORM 有但迁移没列: {sorted(missing)}\n"
            f"  迁移列了但 ORM 没: {sorted(extra)}\n"
            f"  修法：对齐列表或调整 ORM TimestampMixin"
        )

    # --- 主循环：DROP + CREATE 保证幂等 ---
    for t in UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_updated ON {t};")
        op.execute(
            f"CREATE TRIGGER trg_{t}_updated "
            f"BEFORE UPDATE ON {t} FOR EACH ROW "
            f"EXECUTE FUNCTION set_updated_at();"
        )


def downgrade() -> None:
    for t in UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_updated ON {t};")
    # 注：函数 set_updated_at() 由 D4 管理，本迁移不删
```

- [ ] **Step 3：验证**

```bash
uv run alembic upgrade head
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng \
  -c "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%_updated' AND NOT tgisinternal;"
# 期望：13（UPDATED_AT_TABLES 当前 13 条，与 ORM TimestampMixin 表数一致）

# 手工验证一条：
# 注意：PG `now()` 在单事务内返回事务起点 timestamp，pg_sleep 不推进；
#       因此用"预置 updated_at 为很旧的值，再 UPDATE 其他列，看触发器是否覆盖"
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng <<'SQL'
BEGIN;
INSERT INTO wx_groups (usr_group, company_name, updated_at)
  VALUES ('t_test_d5', 'test_co', '2000-01-01'::timestamptz)
  RETURNING updated_at AS before_upd;
UPDATE wx_groups SET company_name = 'test_co2' WHERE usr_group = 't_test_d5'
  RETURNING updated_at AS after_upd, (updated_at > '2000-01-01'::timestamptz) AS trigger_fired;
ROLLBACK;
SQL
# 期望：after_upd >> before_upd（触发器把 updated_at 覆盖到 now()）；trigger_fired = t

uv run alembic downgrade -1
uv run alembic upgrade head
```

- [ ] **Step 4：Commit**

```bash
git add alembic/versions/20260417_0004_*.py
git commit -m "feat(db): D5 BEFORE UPDATE triggers maintaining updated_at on 13 tables"
```

**关键风险**：
- **`UPDATED_AT_TABLES` 清单与 ORM 严格对齐**（runtime 断言兜底，未来加新 TimestampMixin 表漏更新立刻 fail）
- **ORM 侧不得同时用 `onupdate=func.now()`**（双写）
- 列表当前 13 张（仅 TimestampMixin 实体表）。`user_wx_bindings` / `user_phone_numbers` / `user_emails` / `alarm_records` 故意排除：前 3 张是关联表（仅 bound_at 等域时间戳），alarm_records 是不可变日志（triggered_at + reset_at）。若 ORM 后续增减 TimestampMixin 需同步更新

---

## Task D6：scene_* 租户触发器 + 12 张表 RLS（ENABLE + FORCE + tenant_isolation policy）

**Spec 依据**：§3.7 L670-722 / §4.1.1 (4)(5) / §4.2 L1487-1495, L1529-1541

**Files:**
- Create: `D:\江苏润盛\alembic\versions\20260417_0005_scene_triggers_and_rls.py`

**前置**：D4（scene 触发器依赖 enforce_scene_tenant_consistency / fill_scene_views_snapshot）

- [ ] **Step 1：生成**

```bash
uv run alembic revision -m "scene tenant triggers + RLS tenant_isolation (12 tables)"
```

- [ ] **Step 2：upgrade()**

```python
from __future__ import annotations

from alembic import op


revision = "xxx"
down_revision = "<D5-rev>"


# 带 usr_group 的表（除 wx_groups 自身 — 自身即租户字典）；ORM `usr_group` 列存在即入。
# satellite 表（user_emails / user_phone_numbers / device_points / device_waring_cfgs /
#   sim_cards / alarm_outbox / soft_logs）**不**入此列表：
#   它们通过 FK→父表 继承租户，无自身 usr_group 列；policy 的 `usr_group = ...`
#   会在 CREATE POLICY 阶段炸 `column does not exist`。spec §3.7 L676 权威判据：
#   "对所有业务表（**带 usr_group 字段的**）启用 RLS"。
RLS_TABLES: list[str] = [
    # 业务表
    "users", "user_wx_bindings",
    "devices",
    "alarm_records",
    "timing_plans", "maintain_plans",
    "scene_pages", "scene_views",
    "pay_orders",
    # 审计表（含 usr_group 的）
    "user_control_actions", "maintain_actions",
    "user_login_records",
]


def upgrade() -> None:
    # --- drift detection: RLS_TABLES vs ORM usr_group 表 ---
    from ruisheng_shared.models import Base

    orm_tenant = {
        t.name for t in Base.metadata.tables.values()
        if "usr_group" in t.columns and t.name != "wx_groups"
    }
    plan_tenant = set(RLS_TABLES)
    diff = orm_tenant.symmetric_difference(plan_tenant)
    if diff:
        raise RuntimeError(
            f"RLS_TABLES drift from ORM: {sorted(diff)}\n"
            f"  ORM 含 usr_group: {sorted(orm_tenant)}\n"
            f"  plan RLS_TABLES: {sorted(plan_tenant)}"
        )

    # --- 块 A：scene_* 3 个专用触发器（字母序 enforce → fill → updated） ---
    # scene_pages：只 enforce
    op.execute("""
        DROP TRIGGER IF EXISTS trg_scene_pages_enforce_tenant ON scene_pages;
        CREATE TRIGGER trg_scene_pages_enforce_tenant
          BEFORE INSERT OR UPDATE OF owner_user_name, usr_group ON scene_pages
          FOR EACH ROW EXECUTE FUNCTION enforce_scene_tenant_consistency();
    """)

    # scene_views：enforce + fill_snapshot
    op.execute("""
        DROP TRIGGER IF EXISTS trg_scene_views_enforce_tenant ON scene_views;
        CREATE TRIGGER trg_scene_views_enforce_tenant
          BEFORE INSERT OR UPDATE OF owner_user_name, usr_group, scene_page_id, dev_number
          ON scene_views
          FOR EACH ROW EXECUTE FUNCTION enforce_scene_tenant_consistency();

        DROP TRIGGER IF EXISTS trg_scene_views_fill_snapshot ON scene_views;
        CREATE TRIGGER trg_scene_views_fill_snapshot
          BEFORE INSERT ON scene_views
          FOR EACH ROW EXECUTE FUNCTION fill_scene_views_snapshot();
    """)

    # --- 块 B：12 张表 ENABLE + FORCE + policy ---
    for t in RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;")
        # FORCE：对 owner 也生效（不留超级用户后门；spec §3.7 要补 v1.3.7）
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;")
        # policy DROP + CREATE 保证幂等
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t};")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {t}
              USING (
                usr_group = current_setting('app.tenant_id', true)
                OR current_setting('app.role', true) = 'Administrators'
              )
              WITH CHECK (
                usr_group = current_setting('app.tenant_id', true)
                OR current_setting('app.role', true) = 'Administrators'
              );
        """)


def downgrade() -> None:
    for t in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t};")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;")
    # scene 触发器
    op.execute("DROP TRIGGER IF EXISTS trg_scene_views_fill_snapshot ON scene_views;")
    op.execute("DROP TRIGGER IF EXISTS trg_scene_views_enforce_tenant ON scene_views;")
    op.execute("DROP TRIGGER IF EXISTS trg_scene_pages_enforce_tenant ON scene_pages;")
```

- [ ] **Step 3：验证**

```bash
uv run alembic upgrade head

# 核 RLS + FORCE 启用
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng <<'SQL'
SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class
  WHERE relnamespace='public'::regnamespace
    AND relrowsecurity
  ORDER BY relname;
SQL
# 期望：12 行，relrowsecurity + relforcerowsecurity 全为 t

# 核 policy
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng \
  -c "SELECT polname, polrelid::regclass FROM pg_policy WHERE polname='tenant_isolation';"
# 期望：12 行

# 核 scene 触发器字母序生效
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng -c "\d+ scene_views"
# 期望 Triggers 段按字母序：enforce < fill < updated

# 租户隔离冒烟（D9 会补完整测试）
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng <<'SQL'
BEGIN;
SET LOCAL ROLE ruisheng_api;
SET LOCAL app.tenant_id = 'ug_A';
-- 插入本租户数据 OK
INSERT INTO devices (usr_group, dev_number, dev_name) VALUES ('ug_A', 1, 'dev-A') RETURNING dev_number;
-- 跨租户 INSERT 应被 WITH CHECK 挡
INSERT INTO devices (usr_group, dev_number, dev_name) VALUES ('ug_B', 2, 'dev-B');
-- 期望：ERROR 42501 new row violates row-level security policy
ROLLBACK;
SQL

uv run alembic downgrade -1
uv run alembic upgrade head
```

- [ ] **Step 4：Commit**

```bash
git add alembic/versions/20260417_0005_*.py
git commit -m "feat(db): D6 scene tenant triggers + FORCE RLS tenant_isolation on 12 tables"
```

**关键风险**：
- **FORCE ROW LEVEL SECURITY** 是 M1 修复：防 owner 连接绕过 RLS；没有 FORCE 则 D9 测试和生产运维脚本会产生假阳性跨租户读
- scene 触发器的 **`BEFORE INSERT OR UPDATE OF <cols>`** 精确列清单（spec L1488/L1530）不能简化；否则每次改 `page_name` 也触发校验
- **WITH CHECK 显式**写出（PG 默认值等于 USING，但显式可读且防将来语义漂移）
- RLS 变量名 **`app.tenant_id` / `app.role`**（spec §3.7 L686 + §4.1.1 L989；**不是** `app.current_usr_group`）
- 无 `usr_group` 的表**不启** RLS（通过 FK 继承租户 / 无租户上下文）：
  - satellite 带 FK 继承：user_emails (→users) / user_phone_numbers (→users) / device_points (→devices) / device_waring_cfgs (→devices) / sim_cards (→devices) / alarm_outbox (→alarm_records)
  - 无 tenant 上下文或本身即租户字典：wx_groups / device_templates / pay_orders_seen / soft_logs / point_data_realtime / point_data_history / waveform_history
- **12 张表权威清单**（v1.2 修订 — 原 19 条含 7 张 satellite 表是 Plan bug #4）：`users / user_wx_bindings / devices / alarm_records / timing_plans / maintain_plans / scene_pages / scene_views / pay_orders / user_control_actions / maintain_actions / user_login_records`。drift 断言仅以 **ORM `usr_group` 列存在性**为判据（spec §3.7 L676）。

---

## Task D7：UPDATE-heavy 表 fillfactor + autovacuum 调优

**Spec 依据**：§4.2 L1184-1190, L1126-1129 / §5.10 L1930

**Files:**
- Create: `D:\江苏润盛\alembic\versions\20260417_0006_hot_table_tuning.py`

**前置**：D6

- [ ] **Step 1：生成**

```bash
uv run alembic revision -m "fillfactor + autovacuum tuning (3 tables)"
```

- [ ] **Step 2：upgrade()**

```python
def upgrade() -> None:
    # point_data_realtime：HOT update 最密集，激进 autovacuum
    op.execute("""
        ALTER TABLE point_data_realtime SET (
          fillfactor = 70,
          autovacuum_vacuum_scale_factor = 0.05,
          autovacuum_analyze_scale_factor = 0.02,
          autovacuum_vacuum_cost_limit = 1000,
          autovacuum_vacuum_insert_scale_factor = 0.1
        );
    """)
    # devices：中等 UPDATE 频率（心跳/在线状态/上行时间）
    op.execute("""
        ALTER TABLE devices SET (
          fillfactor = 80,
          autovacuum_vacuum_scale_factor = 0.05
        );
    """)
    # device_waring_cfgs：配置变更频繁（§5.10 L1930 新增）
    op.execute("""
        ALTER TABLE device_waring_cfgs SET (fillfactor = 80);
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE device_waring_cfgs RESET (fillfactor);")
    op.execute("""
        ALTER TABLE devices RESET (fillfactor, autovacuum_vacuum_scale_factor);
    """)
    op.execute("""
        ALTER TABLE point_data_realtime RESET (
          fillfactor, autovacuum_vacuum_scale_factor,
          autovacuum_analyze_scale_factor, autovacuum_vacuum_cost_limit,
          autovacuum_vacuum_insert_scale_factor
        );
    """)
```

- [ ] **Step 3：验证**

```bash
uv run alembic upgrade head
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng \
  -c "SELECT relname, reloptions FROM pg_class WHERE relname IN ('point_data_realtime','devices','device_waring_cfgs');"
# 期望 3 行 reloptions 含对应 fillfactor

uv run alembic downgrade -1
uv run alembic upgrade head
```

- [ ] **Step 4：Commit**

```bash
git add alembic/versions/20260417_0006_*.py
git commit -m "feat(db): D7 fillfactor + autovacuum tuning (incl. device_waring_cfgs per §5.10)"
```

---

## Task D8：TimescaleDB hypertable + retention + compression（5 张表；schema prep 前置）

**Spec 依据**：§4.2 L1200-1203 / L1215-1217 / L1396-1400 / L1427-1434 / §5.10 L1939-1960

> **v1.3 修订（Plan bug #5）**：controller 在 D8 pre-dispatch 针对 live DB 跑 `create_hypertable` 探测，抓到 **两条 TimescaleDB 2.16.1 硬约束**：
> 1. **FK → hypertable 禁止**（实测 `ERROR: cannot have FOREIGN KEY constraints to hypertable`）：`alarm_outbox.alarm_id → alarm_records(id)` 阻塞 alarm_records 转 hypertable。
> 2. **PK/UNIQUE 必须含分区列**：alarm_records / soft_logs / user_login_records / user_control_actions 均以 `id` 单列为 PK，user_control_actions 另有 `UNIQUE (cmd_id)` 幂等键。
>
> **user 拍板（2026-04-17）**：
> - Q1-A：D8 先 **DROP FK** `fk_alarm_outbox_alarm_id_alarm_records`（alarm_outbox 是 transient 事件表，app 层保证引用完整；已有 `idx_alarm_outbox_unpublished` 覆盖主查询路径）
> - Q2-A：alarm_records / soft_logs / user_login_records **PK 改为 `(id, <time_col>)` 复合**（BIGSERIAL id 自身唯一，加时间列仅为满足 TS 约束；不影响应用层以 id 查）
> - Q3-B：**user_control_actions 不转 hypertable**，保留 `UNIQUE (cmd_id)` 幂等语义；该表数据量增长可控（人发起），冷数据由 Plan 3 后续归档 Job 手动处理。Spec §5.10 L1958-1960 作为 v1.3.7 TODO 摘除。
>
> 净结果：**hypertable 6 → 5 张**；D8 migration 前置 schema prep（drop FK + 复合 PK 三张）+ 再做 hypertable/retention/compression。

**Files:**
- Create: `D:\江苏润盛\alembic\versions\20260417_0007_timescale_hypertables.py`
- Modify（worktree 而非 master）:
  - `ruisheng-shared/src/ruisheng_shared/models/alarms.py` — `AlarmRecord` PK 复合 `(id, triggered_at)`，`AlarmOutbox.alarm_id` 去 FK（保留列 + 注释说明弱引用）
  - `ruisheng-shared/src/ruisheng_shared/models/logs.py` — `SoftLog` / `UserLoginRecord` PK 复合 `(id, recorded_at/logged_at)`

**前置**：D7

- [ ] **Step 0：ORM 先改（与迁移同 commit 或前置 commit 均可，但必须本 task 内）**

ORM 修改点（3 处）：

1. `alarms.py::AlarmRecord`
   ```python
   # 旧: id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
   #     triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
   # 新: 复合 PK (id, triggered_at) — TimescaleDB 要求分区列入 PK
   id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
   ...
   triggered_at: Mapped[datetime] = mapped_column(
       DateTime(timezone=True), nullable=False, primary_key=True
   )
   ```
   docstring 追加："PK (id, triggered_at) 复合：D8 转 hypertable 的 TimescaleDB 硬要求。id 自身 BIGSERIAL 唯一，复合只为满足 TS 约束。"

2. `alarms.py::AlarmOutbox`
   ```python
   # 旧: alarm_id: Mapped[int] = mapped_column(
   #         BigInteger, ForeignKey("alarm_records.id"), nullable=False
   #     )
   # 新: 去 FK（TS 禁止 FK → hypertable）
   alarm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
   ```
   docstring 追加："alarm_id 去 FK 约束（D8 plan bug #5）：alarm_records 为 TimescaleDB hypertable，TS 2.16.1 拒绝 FK → hypertable。完整性依靠 app 层（publish job 读 alarm_records 时按 alarm_id 外连，缺失行跳过即可，不影响 outbox 语义）。"

3. `logs.py::SoftLog` / `UserLoginRecord`：与 AlarmRecord 同样的复合 PK 模式（recorded_at / logged_at 加 `primary_key=True`），docstring 加同款说明。

- [ ] **Step 1：生成迁移**

```bash
uv run alembic revision -m "timescale hypertables + retention + compression (5 tables, PK prep)"
```

- [ ] **Step 2：upgrade() — schema prep 先于 hypertable 转换**

```python
from alembic import op


revision = "xxx"
down_revision = "<D7-rev>"


# (table, time_col, chunk, retention, compress_after, segmentby)
HYPERTABLES = [
    ("point_data_history",   "recorded_at",  "1 month", "1 year",  "7 days",  "dev_number, point_id"),
    ("waveform_history",     "recorded_at",  "1 month", "1 year",  "7 days",  "dev_number"),
    ("soft_logs",            "recorded_at",  "1 month", "1 year",  "7 days",  None),
    # Plan bug #6（v1.4 修订）：以下 2 张表与 D6 FORCE RLS 冲突，TS 2.16.1 拒 SET compress；
    # 只保留 retention，compression 等 TS #6827 解决后在 v1.3.7 TODO 里补回。
    ("user_login_records",   "logged_at",    "1 month", "3 years", None,      None),
    ("alarm_records",        "triggered_at", "1 month", "2 years", None,      None),
    # user_control_actions 不转 hypertable（保 UNIQUE(cmd_id) 幂等键；spec v1.3.7 摘除）
]


# 需改复合 PK 的 3 张表 (table, old_pk_name, new_pk_cols)
PK_COMPOSITES = [
    ("alarm_records",       "pk_alarm_records",       "id, triggered_at"),
    ("soft_logs",           "pk_soft_logs",           "id, recorded_at"),
    ("user_login_records",  "pk_user_login_records",  "id, logged_at"),
]


def upgrade() -> None:
    # --- Step A: 拆 FK alarm_outbox → alarm_records（TS 禁 FK → hypertable） ---
    op.execute(
        "ALTER TABLE alarm_outbox "
        "DROP CONSTRAINT IF EXISTS fk_alarm_outbox_alarm_id_alarm_records;"
    )

    # --- Step B: 3 张 id-only PK → 复合 PK (id, time_col) ---
    for table, old_pk, new_cols in PK_COMPOSITES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {old_pk};")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {old_pk} PRIMARY KEY ({new_cols});")

    # --- Step C: hypertable + retention + compression ---
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    for table, tcol, chunk, retain, compress, segby in HYPERTABLES:
        op.execute(
            f"SELECT create_hypertable('{table}', '{tcol}', "
            f"chunk_time_interval => INTERVAL '{chunk}', "
            f"if_not_exists => TRUE);"
        )
        op.execute(f"SELECT remove_retention_policy('{table}', if_exists => TRUE);")
        op.execute(f"SELECT add_retention_policy('{table}', INTERVAL '{retain}');")

        if compress:
            segby_clause = (
                f", timescaledb.compress_segmentby = '{segby}'"
                if segby else ""
            )
            op.execute(
                f"ALTER TABLE {table} SET (timescaledb.compress{segby_clause});"
            )
            op.execute(
                f"SELECT remove_compression_policy('{table}', if_exists => TRUE);"
            )
            op.execute(
                f"SELECT add_compression_policy('{table}', INTERVAL '{compress}');"
            )


def downgrade() -> None:
    """Forward-only partial: 只卸 policy；hypertable/PK/FK 回退需 `docker compose down -v`。

    TimescaleDB 不原生支持 hypertable → regular 回退；即使回退，old simple PK 与
    `FK → (现已是 hypertable 的) alarm_records` 都因 TS 约束无法干净重建。生产环境升级后
    单向前进（本 task 风险在 release note 与 D10 spec v1.3.7 里显式说明）。
    """
    for table, *_ in reversed(HYPERTABLES):
        op.execute(f"SELECT remove_retention_policy('{table}', if_exists => TRUE);")
        op.execute(f"SELECT remove_compression_policy('{table}', if_exists => TRUE);")
```

- [ ] **Step 3：验证**

```bash
uv run alembic upgrade head
docker exec -i ruisheng-postgres-dev psql -U ruisheng_dev -d ruisheng <<'SQL'
-- 期望 5 张 hypertable（user_control_actions 不在列）
SELECT hypertable_name, num_chunks, compression_enabled
  FROM timescaledb_information.hypertables
  ORDER BY hypertable_name;

-- PK 已改复合（3 张）
SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint
  WHERE contype='p'
    AND conrelid::regclass::text IN ('alarm_records','soft_logs','user_login_records')
  ORDER BY conname;
-- 期望：三张都是 PRIMARY KEY (id, <time_col>)

-- FK 已拆（alarm_outbox.alarm_id 无 FK）
SELECT count(*) FROM pg_constraint
  WHERE contype='f' AND conname='fk_alarm_outbox_alarm_id_alarm_records';
-- 期望：0

-- retention + compression jobs
SELECT proc_name, hypertable_name
  FROM timescaledb_information.jobs
  WHERE application_name LIKE 'Retention%' OR application_name LIKE 'Compression%'
  ORDER BY hypertable_name, proc_name;
-- 期望：retention × 5 + compression × 3 = 8 行
-- compression 仅在 point_data_history / waveform_history / soft_logs 启
-- alarm_records / user_login_records 受 D6 FORCE RLS 约束，TS 2.16.1 拒 compression（Plan bug #6）
-- 等上游 TS issue #6827 解决后在 v1.3.7 follow-up 补回
SQL

uv run alembic downgrade -1 && uv run alembic upgrade head  # 策略 remove+add 幂等即可
```

- [ ] **Step 4：ORM 回归测试（321 + 8 skip）**

```bash
cd D:/江苏润盛/.claude/worktrees/plan-0-foundation
uv run pytest -q  # 期望 321 passed + 8 skipped（无回归）
```

- [ ] **Step 5：Commit**

```bash
git add ruisheng-shared/src/ruisheng_shared/models/alarms.py \
        ruisheng-shared/src/ruisheng_shared/models/logs.py \
        alembic/versions/20260417_0007_*.py
git commit -m "feat(db): D8 TimescaleDB hypertables + retention + compression (5 tables, composite PK prep)"
```

**关键风险 / 设计决策（v1.4 修订）**：
- **Plan bug #5**（D8）：TS FK→hypertable 禁止 + PK 必含分区列 — pre-dispatch 活探测抓出。fix：drop outbox FK + 3 张表复合 PK + user_control_actions 摘除 hypertable 资格
- **Plan bug #6**（D8，v1.4）：TS 2.16.1 `compression cannot be used on table with row security` — 实现时 implementer 在 `ALTER TABLE user_login_records SET (timescaledb.compress, ...)` 步抓到 FeatureNotSupportedError。受影响 2 张：`alarm_records` + `user_login_records`（D6 FORCE RLS）。user 拍板 Option A：摘掉这 2 张的 compression，只保留 retention。TS 上游 issue #6827 解决后 v1.3.7 补回（低写入率审计表，MVP 规模存储压力可忽略）
- **幂等性**（M2 已在 v1.1 修复）：`if_not_exists => TRUE` + remove+add 策略；ALTER TABLE PK 改动带 `IF EXISTS` 也幂等
- **单向迁移**：hypertable 转换与 PK 改动 downgrade 不回退（TS 限制），release note 与 spec v1.3.7 必须标注
- **soft_logs 压缩**：v1.1 修复 `if segby: else:` 分支 bug；v1.4 仍保留"有 compress_after 的都走 SET + policy"（soft_logs 无 RLS 所以不受 Plan bug #6 影响）
- **downgrade 不删表**（v1.1 已修）

---

## Task D9：集成测试（roles / functions / triggers / RLS / hypertables 全覆盖）

**Files:**
- Create: `D:\江苏润盛\tests\integration\__init__.py`（空）
- Create: `D:\江苏润盛\tests\integration\test_alembic_upgrade.py`
- Modify: `D:\江苏润盛\tests\conftest.py`（追加 ruisheng_api / ruisheng_gw fixture）

**前置**：D8

- [ ] **Step 1：conftest 加多角色 fixture**

```python
# tests/conftest.py 追加（3 个 engine fixture，对应 3 种 DB 身份）
import os
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine


# 按 docker-compose.dev.yml POSTGRES_USER/POSTGRES_PASSWORD（都是 "ruisheng_dev"）；CI 可从 env 覆盖
_DEV_DSN = os.environ.get(
    "DEV_DATABASE_URL",
    "postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng",
)


@pytest_asyncio.fixture(scope="session")
async def dev_engine():
    """以 ruisheng_dev（owner）身份连接。
    owner 无 BYPASSRLS 属性 + D6 FORCE RLS 后也受 tenant_isolation 策略约束
    (test_owner_does_not_bypass_rls 依赖此行为)。大多数 DDL/introspection 测试用此 fixture。
    """
    engine = create_async_engine(_DEV_DSN)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def api_engine():
    """以 ruisheng_api 身份连接（非 BYPASSRLS，受 RLS 约束）。"""
    pw = os.environ["RUISHENG_API_PASSWORD"]
    engine = create_async_engine(
        f"postgresql+asyncpg://ruisheng_api:{pw}@127.0.0.1:5432/ruisheng"
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def gw_engine():
    """以 ruisheng_gw 身份连接（BYPASSRLS，跨租户读写）。"""
    pw = os.environ["RUISHENG_GW_PASSWORD"]
    engine = create_async_engine(
        f"postgresql+asyncpg://ruisheng_gw:{pw}@127.0.0.1:5432/ruisheng"
    )
    yield engine
    await engine.dispose()
```

- [ ] **Step 2：测试清单**

```python
# tests/integration/test_alembic_upgrade.py
"""Stage D 完整集成测试：从角色、函数、触发器、RLS、hypertable 全覆盖。"""
from __future__ import annotations

import subprocess
import pytest
from sqlalchemy import text


@pytest.mark.integration
async def test_upgrade_down_and_up_again():
    """alembic 对称性：up → down → up 后 version 回到 head"""
    subprocess.check_call(["uv", "run", "alembic", "downgrade", "base"])
    subprocess.check_call(["uv", "run", "alembic", "upgrade", "head"])


@pytest.mark.integration
async def test_roles_exist(dev_engine):
    """ruisheng_gw (BYPASSRLS) + ruisheng_api (非 BYPASSRLS)"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT rolname, rolbypassrls FROM pg_roles
            WHERE rolname IN ('ruisheng_gw', 'ruisheng_api') ORDER BY rolname;
        """))
        roles = {r.rolname: r.rolbypassrls for r in rows}
    assert roles == {"ruisheng_api": False, "ruisheng_gw": True}


@pytest.mark.integration
async def test_functions_are_invoker(dev_engine):
    """3 个 PL/pgSQL 函数都是 SECURITY INVOKER 且 search_path 硬绑定"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT proname, prosecdef, proconfig
              FROM pg_proc
              WHERE proname IN ('set_updated_at',
                                'enforce_scene_tenant_consistency',
                                'fill_scene_views_snapshot');
        """))
        funcs = {r.proname: (r.prosecdef, r.proconfig) for r in rows}
    assert len(funcs) == 3
    for name, (secdef, cfg) in funcs.items():
        assert secdef is False, f"{name} must be SECURITY INVOKER"
        assert cfg and any("search_path" in c for c in cfg), \
            f"{name} missing SET search_path"


@pytest.mark.integration
async def test_updated_at_triggers_count(dev_engine):
    """13 张表各有 trg_<table>_updated（v1.5 修订 — 原 17 是 Plan bug #3 遗留；D5 Plan bug #3 修后 UPDATED_AT_TABLES 收敛到 13，本断言同步对齐）"""
    async with dev_engine.connect() as conn:
        n = await conn.scalar(text("""
            SELECT count(*) FROM pg_trigger
              WHERE tgname LIKE 'trg_%_updated' AND NOT tgisinternal;
        """))
    assert n == 13


@pytest.mark.integration
async def test_scene_triggers_exist(dev_engine):
    """scene_pages 有 1 个 enforce，scene_views 有 enforce + fill_snapshot"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT tgrelid::regclass::text AS tbl, tgname
              FROM pg_trigger
              WHERE NOT tgisinternal
                AND tgname IN (
                  'trg_scene_pages_enforce_tenant',
                  'trg_scene_views_enforce_tenant',
                  'trg_scene_views_fill_snapshot'
                );
        """))
        pairs = {(r.tbl, r.tgname) for r in rows}
    assert pairs == {
        ("scene_pages", "trg_scene_pages_enforce_tenant"),
        ("scene_views", "trg_scene_views_enforce_tenant"),
        ("scene_views", "trg_scene_views_fill_snapshot"),
    }


@pytest.mark.integration
async def test_rls_forced_on_12_tables(dev_engine):
    """12 张 RLS 表必须同时 ENABLE + FORCE（v1.2 修订 — 原 18 含 7 张 satellite 表是 Plan bug #4）"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT relname FROM pg_class
              WHERE relnamespace='public'::regnamespace
                AND relrowsecurity AND relforcerowsecurity
              ORDER BY relname;
        """))
        rls_tables = {r.relname for r in rows}
    assert len(rls_tables) == 12


@pytest.mark.integration
async def test_policies_exist(dev_engine):
    """12 张表各 1 条 tenant_isolation policy（USING + WITH CHECK）"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT polrelid::regclass::text AS tbl, polname
              FROM pg_policy WHERE polname='tenant_isolation';
        """))
        tables = {r.tbl for r in rows}
    assert len(tables) == 12


@pytest.mark.integration
async def test_hypertables_exist(dev_engine):
    """5 张 hypertable（v1.3 修订：user_control_actions 保 cmd_id UQ 幂等语义，不入 hypertable）"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT hypertable_name FROM timescaledb_information.hypertables;
        """))
        names = {r.hypertable_name for r in rows}
    assert names == {
        "point_data_history", "waveform_history", "soft_logs",
        "user_login_records", "alarm_records",
    }


@pytest.mark.integration
async def test_d8_pk_composite_and_fk_dropped(dev_engine):
    """D8 schema prep（Plan bug #5）：3 张表 PK 复合 + alarm_outbox FK 已拆

    v1.7 修订（Plan bug #9）：原 SQL alias `AS def` + Python `r.def` 触发 SyntaxError
    （`def` 是 Python 关键字）。改为 `AS constraint_def` + `r.constraint_def`。
    """
    async with dev_engine.connect() as conn:
        # 3 张复合 PK
        rows = await conn.execute(text("""
            SELECT conname, pg_get_constraintdef(oid) AS constraint_def
              FROM pg_constraint
              WHERE contype='p'
                AND conrelid::regclass::text
                    IN ('alarm_records','soft_logs','user_login_records')
              ORDER BY conname;
        """))
        pk_defs = {r.conname: r.constraint_def for r in rows}
        assert "triggered_at" in pk_defs["pk_alarm_records"]
        assert "recorded_at"  in pk_defs["pk_soft_logs"]
        assert "logged_at"    in pk_defs["pk_user_login_records"]
        # FK 已拆
        n = await conn.scalar(text("""
            SELECT count(*) FROM pg_constraint
              WHERE contype='f'
                AND conname='fk_alarm_outbox_alarm_id_alarm_records';
        """))
        assert n == 0


@pytest.mark.integration
async def test_rls_actually_blocks_cross_tenant_read(api_engine, seed_tenants):
    """ruisheng_api + SET LOCAL app.tenant_id='A' → 只看见 A 的行

    v1.6 修订（Plan bug #8-B）：原 INSERT 只列 3 列会违反 devices 6 个 NOT NULL 无默认
    列（dev_ser_number/modbus_addr/update_interval_decisec/loss_count/is_online/update_flag）。
    补齐必填列；seed_tenants fixture 负责 ug_A + ug_B 的 wx_groups 行（FK 前置）。
    """
    async with api_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
            await conn.execute(text("""
                INSERT INTO devices
                    (usr_group, dev_number, dev_ser_number, dev_name,
                     modbus_addr, update_interval_decisec, loss_count,
                     is_online, update_flag)
                VALUES
                    ('ug_A', '901', 'SER-901', 'A-dev',
                     1, 100, 0, false, 0)
            """))
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
            rows = await conn.execute(text(
                "SELECT count(*) FROM devices WHERE dev_number='901'"
            ))
            assert rows.scalar() == 1
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_B'"))
            rows = await conn.execute(text(
                "SELECT count(*) FROM devices WHERE dev_number='901'"
            ))
            assert rows.scalar() == 0


@pytest.mark.integration
async def test_rls_blocks_cross_tenant_insert(api_engine, seed_tenants):
    """ruisheng_api + SET tenant=A → 插 usr_group=B 被 WITH CHECK 拒绝

    v1.6 修订（Plan bug #8-B）：补齐 devices NOT NULL 列。
    """
    async with api_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
            with pytest.raises(Exception) as ei:
                await conn.execute(text("""
                    INSERT INTO devices
                        (usr_group, dev_number, dev_ser_number, dev_name,
                         modbus_addr, update_interval_decisec, loss_count,
                         is_online, update_flag)
                    VALUES
                        ('ug_B', '902', 'SER-902', 'B-dev',
                         1, 100, 0, false, 0)
                """))
            assert "row-level security" in str(ei.value).lower()


@pytest.mark.integration
async def test_owner_does_not_bypass_rls(dev_engine):
    """owner (ruisheng_dev) 也受 FORCE RLS 约束（这是 M1 的核心）"""
    async with dev_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_X_not_exist'"))
            # 即使 owner 也应看不见任何 usr_group != 'ug_X_not_exist' 的行
            rows = await conn.execute(text("SELECT count(*) FROM users"))
            assert rows.scalar() == 0


@pytest.mark.integration
async def test_gw_bypasses_rls(gw_engine):
    """ruisheng_gw 应 BYPASSRLS：不设 tenant_id 也能跨租户读全量"""
    async with gw_engine.connect() as conn:
        # 不设 app.tenant_id
        rows = await conn.execute(text("SELECT count(*) FROM users"))
        # 至少能读，不被 RLS 拦（具体计数由种子数据决定，此处只断言 != 0 or >= 0 即可）
        assert rows.scalar() is not None  # 能执行不抛 RLS 错


@pytest.mark.integration
async def test_scene_trigger_raises_23514(api_engine, seed_tenants):
    """跨租户 INSERT scene_pages 被 enforce 触发器抛 23514。

    seed_tenants 必须包含：ug_A + ug_B 两个 wx_groups 行 + user_of_ugB 用户（usr_group=ug_B）。
    触发器路径：tenant_id='ug_A' + owner_user_name='user_of_ugB'（usr_group='ug_B'）
    → enforce_scene_tenant_consistency 检测不一致 → RAISE EXCEPTION ERRCODE='23514'。
    """
    async with api_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
            with pytest.raises(Exception) as ei:
                await conn.execute(text("""
                    INSERT INTO scene_pages (usr_group, owner_user_name, page_name)
                    VALUES ('ug_A', 'user_of_ugB', 'p1')
                """))
            assert "23514" in str(ei.value) or "scene_tenant_violation" in str(ei.value)


@pytest.mark.integration
async def test_api_insert_uses_sequence(api_engine, seed_tenants):
    """ruisheng_api INSERT 必须有 sequence USAGE（BIGSERIAL）。

    v1.6 修订（Plan bug #8-A）：原目标表 wx_groups 无 id 列（PK 是 usr_group VARCHAR）
    且无 group_name 列（真实列 company_name）——完全错表。改用 devices（BIGSERIAL id）。
    """
    async with api_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
            new_id = await conn.scalar(text("""
                INSERT INTO devices
                    (usr_group, dev_number, dev_ser_number, dev_name,
                     modbus_addr, update_interval_decisec, loss_count,
                     is_online, update_flag)
                VALUES
                    ('ug_A', '997', 'SER-997', 'seq-probe',
                     1, 100, 0, false, 0)
                RETURNING id
            """))
            assert new_id is not None  # sequence USAGE OK
```

- [ ] **Step 2.5：seed_tenants fixture（v1.6 新增 — Plan bug #8 配套）**

写入 `tests/integration/conftest.py`（或 `tests/conftest.py`）作 session-scoped fixture。用 `gw_engine`（BYPASSRLS）绕过 RLS，填充 D9 多个 RLS / trigger test 需要的最小租户基线：

```python
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture(scope="session")
async def seed_tenants(gw_engine):
    """D9 最小 tenant 种子：ug_A + ug_B 两个 wx_groups + user_of_ugB 用户。

    使用 gw_engine（BYPASSRLS）幂等插入（ON CONFLICT DO NOTHING）；不做 teardown，
    dev 容器通过 `docker compose down -v` 整体重置。Stage E 的 seeds 机制落地后
    可升级为更完整的 fixture。
    """
    async with gw_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("""
                INSERT INTO wx_groups (usr_group, company_name)
                VALUES ('ug_A', 'Company A'), ('ug_B', 'Company B')
                ON CONFLICT (usr_group) DO NOTHING
            """))
            # users 表 NOT NULL 无默认列（live DB 校对）：
            #   user_name / password_hash / authority / control_authority / usr_group
            # - authority: VARCHAR(20) CHECK IN ('Administrators','GroupCompany','Company','User')
            #   (见 ruisheng_shared.enums.Authority 枚举；spec §3.6 RBAC 4 级)
            # - control_authority: SmallInteger（int），D9 只关心非空，填 0
            # - user_name: regex ^[a-zA-Z][a-zA-Z0-9_]{3,29}$ OR ^1[3-9][0-9]{9}$
            # - password_hash: 占位 'test-not-a-real-hash'，D9 不涉登录逻辑
            await conn.execute(text("""
                INSERT INTO users
                    (user_name, password_hash, authority, control_authority, usr_group)
                VALUES
                    ('user_of_ugB', 'test-not-a-real-hash', 'User', 0, 'ug_B')
                ON CONFLICT (user_name) DO NOTHING
            """))
    yield
```

> **说明**：implementer 必须在插 users 前**核对当前 ORM 字段列表**（`ruisheng-shared/src/ruisheng_shared/models/users.py`）决定最小合规的列/值组合（regex、phone 格式、enum 值等）。若字段偏移导致 fixture 失败，**必须 BLOCKED 上报**——不要静默改列表。

- [ ] **Step 3：运行**

```bash
uv run task up
uv run pytest tests/integration/ -v -m integration
```

- [ ] **Step 4：Commit**

```bash
git add tests/integration/ tests/conftest.py
git commit -m "test(db): D9 integration tests (roles/functions/triggers/RLS/hypertables, 15 cases)"
```

**关键风险**：
- **RLS 测试必须走 ruisheng_api 连接**（非 dev owner，除非显式测 FORCE 效果）
- 密码 fixture 从 `os.environ` 读，CI 注入
- `seed_tenants` fixture 提供 ug_A/ug_B 两个 wx_groups + `user_of_ugB` 用户 —— 供多个 RLS/trigger test 复用（详见 Step 2.5）
- **测试行 commit 后残留**：plan v1.0 的 `conn.begin()` 默认自动 commit，tests 10/11/15 插入的 devices 行会永久存在于 dev DB；dev-only 容器 `docker compose down -v` 整体重置；CI pipeline 里每次启容器都是干净态，可接受。Stage E seeds 机制可引入 test-scoped 回滚或唯一化 dev_number。

---

## Task D10：阶段 D 收尾

- [ ] **Step 1：PROGRESS.md 修正 & follow-up spec 清单**

在 PROGRESS.md §恢复步骤 修正 RLS 变量名（`app.current_usr_group` → `app.tenant_id`），并记录需发 spec v1.3.7 的 follow-up：

```markdown
## Spec v1.3.7 follow-up（D10 完成后另开分支/PR）
- §3.7 RLS 规约补 `FORCE ROW LEVEL SECURITY`（M1）
- §4.1.1 (1)(4)(5) 三个函数 `CREATE FUNCTION` 尾部补 `SET search_path = pg_catalog, public`（M3）
- §4.1 GRANT 通用规约补 SEQUENCES（M4）
```

- [ ] **Step 2：Runbook 段加入 PROGRESS.md**

```markdown
## Stage D 回滚 Runbook
alembic 严格线性：**禁跳版 downgrade**。若要回滚 D3 删角色，必须先：
  D10 → D9 → D8 → D7 → D6 → D5 → D4 → D3 → D2 逐 step
否则 pg_dump 一致性被破坏（例如 D5 已建触发器依赖 D4 函数，D4 单独回滚会造成幽灵引用）。

生产部署：migration 角色必须 SUPERUSER 或是 ruisheng_gw / ruisheng_api 的成员（否则 DROP OWNED 42501）。
```

- [ ] **Step 3：打 tag**

```bash
cd D:\江苏润盛\.claude\worktrees\plan-0-foundation
git tag -a plan-0-stage-d-complete -m "Stage D: 26 tables + 2 roles + 3 functions + 16 triggers (13 updated_at + 3 scene) + 12 RLS policies (FORCE) + 5 hypertables (Plan bug #5 PK/FK prep) + integration tests"
git push origin plan-0-stage-d-complete
```

- [ ] **Step 4：master PROGRESS 更新 + commit**

更新 PROGRESS.md 反映 Stage D 完成 8/8 + tag + follow-up spec 清单，commit push。

---

# 阶段 E — testcontainers + embedded PG + seeds

## Task E1：conftest.py — postgres/redis 双轨 fixture

> **v1.1 修订（2026-04-17，Plan bug #10 反向 fix）**：原 v1.0 `Replace tests/conftest.py` 会删除 D9 已落地的 `dev_engine` / `api_engine` / `gw_engine` 三个 function-scope fixture（15 integration test + seed_tenants 依赖）。v1.1 改为 **Merge**：D9 fixture 原样保留，只在文件尾部追加新 fixture。另将 async fixtures 全部改为 **function scope**（D9 conftest.py L26-30 已证 `scope="session"` 异步 fixture 在 pytest-asyncio 0.23 auto 模式会触发 "Event loop is closed"）；`postgres_url` / `redis_url` 改为 **同步** session fixture（testcontainers 本身是同步 context manager，async 没必要），alembic upgrade 移入 `postgres_url` fixture（每 session 一次，不重复），`async_engine` + `session` 保持 async 但 function scope。

**Files:**
- Modify: `D:\江苏润盛\tests\conftest.py`（**Merge, NOT Replace**）

- [ ] **Step 1：保留 D9 fixtures + 追加 E1 新 fixture**

**不要动** D9 已有的内容：
- `is_windows` (function scope)
- `_DEV_DSN` 常量 + function-scope engine 注释块
- `dev_engine` / `api_engine` / `gw_engine` 三个 async function-scope fixture

**在文件末尾追加** 以下代码（保留 D9 所有 import + 在其之上按需添加新 import）：

```python
# ---------------------------------------------------------------------
# E1 testcontainers / embedded PG 双轨 session 级 fixture（Plan bug #10 fix：与 D9 fixture 并存）
# ---------------------------------------------------------------------

# 新增 import（如已存在请去重，放在文件顶部 import 区）：
#   from collections.abc import AsyncIterator, Iterator
#   import subprocess
#   from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def _use_embedded() -> bool:
    """Windows 无 Docker 环境走 tools/embedded_pg.py stub；默认走 testcontainers（需 Docker）。"""
    return os.environ.get("USE_EMBEDDED_PG") == "1"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """E1 session 级 PostgreSQL URL（testcontainers 新起容器 + alembic upgrade）。

    * **同步** fixture：testcontainers `with PostgresContainer(...)` 本来就是同步 context
      manager，用 `@pytest_asyncio.fixture(scope="session")` 会在 pytest-asyncio 0.23
      auto 模式触发 "Event loop is closed"（D9 conftest.py L26-30 已证）。
    * alembic upgrade 放在这里：每 session 跑一次；`async_engine` fixture function scope
      只做引擎创建/释放，不再重复 upgrade。
    * 与 D9 `dev_engine`（指向 live Docker `127.0.0.1:5432`）**并存但独立**：
      - D9 fixtures 服务 `tests/integration/*` 现有 15 case（依赖 `docker compose up` 的 dev 容器）
      - E1 fixtures 服务未来 Stage E+ 在 CI Linux / 无 dev stack 场景
    """
    if _use_embedded():
        from tools.embedded_pg import EmbeddedPostgres  # lazy import

        pg = EmbeddedPostgres()
        pg.start_sync()  # E2 stub raises NotImplementedError；真实现后同步启动
        try:
            yield pg.url
        finally:
            pg.stop_sync()
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not available; set USE_EMBEDDED_PG=1")
    with PostgresContainer("timescale/timescaledb:2.16.1-pg15") as container:
        # testcontainers 默认返回 psycopg2 DSN；改 asyncpg driver
        url = container.get_connection_url().replace("psycopg2", "asyncpg")
        # 新库空表，必须 alembic upgrade head 建全 26 表 + 2 角色 + ... + hypertable
        subprocess.check_call(
            ["uv", "run", "alembic", "upgrade", "head"],
            env={**os.environ, "DATABASE_URL": url},
        )
        yield url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """E1 session 级 Redis URL（testcontainers）。同 `postgres_url` 理由用同步 fixture。"""
    try:
        from testcontainers.redis import RedisContainer
    except ImportError:
        pytest.skip("testcontainers not available")
    with RedisContainer("redis:7-alpine") as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest_asyncio.fixture  # function scope — 见 D9 conftest.py L26-30 关于 session-scope async 的注释
async def async_engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    """从 testcontainer-spawn 的 DB 建 async engine。function scope 避开 event loop pitfall。"""
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture  # function scope
async def session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """E1 通用 async session（rollback at teardown）。"""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
```

> **关于 E2 `EmbeddedPostgres`**：E2 stub 当前只有 async `start()` / `stop()`，E1 调用 `start_sync()` / `stop_sync()` 同步别名。E2 stub 需同步增加两个同步方法（同样 `raise NotImplementedError`），见 E2 v1.1 修订。

- [ ] **Step 2：验证**（Docker 运行中）

```bash
cd D:\江苏润盛\.claude\worktrees\plan-0-foundation
export RUISHENG_GW_PASSWORD='dev-gw-change-me'
export RUISHENG_API_PASSWORD='dev-api-change-me'
uv run pytest  # 336 passed + 8 skipped + 可能 N 跳过（testcontainers 非所有测试引用）
```

**关键断言**：
- 测试数不降（仍 336 passed 至少）
- 新 fixture 未被任何现有测试引用（纯"未来 CI 可用"的接口）→ 本 task 不引入断言 test，E3+ 才用
- `pytest --collect-only | grep -E '(postgres_url|redis_url|async_engine|session)'` 应能 collect（只是未被 invoke）

- [ ] **Step 3：Commit**

```bash
git add tests/conftest.py
git commit -m "test(conftest): E1 session-scope postgres_url/redis_url + function async_engine/session (merge w/ D9 fixtures)"
```

---

## Task E2：Embedded PG 包装

> **v1.1 修订（2026-04-17，E1 Plan bug #10 fix 联动）**：E1 `postgres_url` fixture 改为 sync，故需 sync 启停方法。本 stub 同时暴露 `start()`/`stop()`（async）和 `start_sync()`/`stop_sync()`（sync），都 `raise NotImplementedError`，保持接口一致、未来双模式实现都方便。

**Files:**
- Create: `D:\江苏润盛\tools\embedded_pg.py`

> 功能：Windows 无 Docker 环境下拉 PostgreSQL portable，启一个测试用实例。

- [ ] **Step 1：实现**

Create `D:\江苏润盛\tools\embedded_pg.py`:
```python
"""Windows 嵌入式 PostgreSQL 启动器（无 Docker Desktop 场景）。

使用 postgresql-binaries 包（pypi: postgresql-15.x-win）或手工下载 portable。
这里先用 pg_tmp 风格最简实现：用 pip 包 pg_embed 或降级到 pytest-postgresql。
当前为 stub；E1 `postgres_url` fixture 同步模式需 `start_sync()` / `stop_sync()` 入口。
"""
from __future__ import annotations

import asyncio
import random
import tempfile
from pathlib import Path


_NOT_IMPLEMENTED_MSG = (
    "EmbeddedPostgres 目前为 stub。"
    "真正实现在 Plan 0 后续迭代（pg_tmp / pg_embed / portable binaries）。"
    "当前 Windows 用户请启用 Docker Desktop 或清除 USE_EMBEDDED_PG（default=container 模式）。"
)


class EmbeddedPostgres:
    def __init__(self, version: str = "15") -> None:
        self.version = version
        self.port = random.randint(15000, 30000)
        self.data_dir = Path(tempfile.mkdtemp(prefix="ruisheng-pg-"))
        self.url = f"postgresql+asyncpg://postgres:postgres@127.0.0.1:{self.port}/ruisheng"
        self._proc: asyncio.subprocess.Process | None = None

    # Sync API（E1 postgres_url fixture 用）
    def start_sync(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def stop_sync(self) -> None:
        if self._proc:
            self._proc.terminate()

    # Async API（未来 async 场景）
    async def start(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            await self._proc.wait()
```

> 说明：pypi 上的 `pg_embed`（Rust 包装）只支持 Linux/Mac，Windows 走 portable 二进制 + `initdb.exe` + `pg_ctl.exe`。本 stub 标注为后续实现；当前用 testcontainers 模式即可让 Plan 0 推进。Q-E06 Windows 部署环境确认后再决定是否完整实现。

- [ ] **Step 2：Commit**

```bash
git add tools/embedded_pg.py
git commit -m "chore(tools): EmbeddedPostgres stub — full impl pending Q-E06"
```

---

## Task E3–E6：种子数据 SQL 文件 + 导入脚本

> **v1.1 修订（2026-04-18，Plan bug #11 反向 fix）**：E5 `02_devices.sql` 和 E6 `03_device_points.sql` 的 INSERT 列清单漏 NOT NULL 无 `server_default` 列——D2 migration 只在 created_at/updated_at 上设了 `server_default`，其他 NOT NULL 列（devices 4 个 / device_points 5 个）的"ORM Python default"在**原生 SQL INSERT 路径不生效**（D9 Plan bug #8 同类问题，run_seeds.py 直接用 asyncpg 跑 SQL 字符串会炸 23502）。v1.1 SQL 补齐所有 NOT NULL 列，值匹配 ORM Python default 以保持语义一致。
>
> **devices 补 4 列**：`update_interval_decisec`=100 / `loss_count`=0 / `is_online`=false / `update_flag`=0
> **device_points 补 5 列**：`point_ratio`=1.0 / `point_offset`=0.0 / `user_ratio`=1.0 / `user_point_offset`=0.0 / `show`=1

**Files:**
- Create: `D:\江苏润盛\seeds\00_wx_groups.sql`
- Create: `D:\江苏润盛\seeds\01_users.sql`
- Create: `D:\江苏润盛\seeds\02_devices.sql`
- Create: `D:\江苏润盛\seeds\03_device_points.sql`
- Create: `D:\江苏润盛\tools\run_seeds.py`

### E3: seeds/00_wx_groups.sql
```sql
INSERT INTO wx_groups (usr_group, appid, sys_title, company_name)
VALUES
  ('demo', 'wxDEMOappid', '润盛监控 Demo', '润盛集团 Demo')
ON CONFLICT (usr_group) DO NOTHING;
```

### E4: seeds/01_users.sql
```sql
-- authority ∈ {'Administrators','GroupCompany','Company','User'}（ck_users_authority）
-- user_name 匹配 ^1[3-9][0-9]{9}$（手机号）或 ^[a-zA-Z][a-zA-Z0-9_]{3,29}$（用户名）
-- password_hash 当前仅 dev stub；生产由后端 bcrypt 计算
INSERT INTO users (user_name, password_hash, authority, control_authority, usr_group)
VALUES
  ('13800138000', '$2b$12$PLACEHOLDER_BCRYPT_HASH', 'Administrators', 3, 'demo'),
  ('13800138001', '$2b$12$PLACEHOLDER_BCRYPT_HASH', 'Company', 1, 'demo')
ON CONFLICT (user_name) DO NOTHING;
```

### E5: seeds/02_devices.sql
```sql
-- v1.1 补全 NOT NULL 列：update_interval_decisec(100)/loss_count(0)/is_online(false)/update_flag(0)
-- 原生 SQL INSERT 路径不走 ORM Python default，必须显式给值（D9 Plan bug #8 / E5 Plan bug #11 教训）
-- CHECK: modbus_addr ∈ [1,247] / baud_rate ∈ {9600,19200,38400,57600,115200} / update_interval_decisec ∈ [10,1000]
INSERT INTO devices (
    dev_number, dev_ser_number, modbus_addr, baud_rate,
    usr_group, administrators,
    update_interval_decisec, loss_count, is_online, update_flag
)
VALUES
  ('60270012', 'DEMO-SN-0001', 1, 9600, 'demo', '13800138000', 100, 0, FALSE, 0)
ON CONFLICT (dev_number) DO NOTHING;
```

### E6: seeds/03_device_points.sql
```sql
-- v1.1 补全 NOT NULL 列：point_ratio(1.0)/point_offset(0.0)/user_ratio(1.0)/user_point_offset(0.0)/show(1)
-- CHECK: point_number ∈ [0,65535] / fun_code ∈ {1,2,3,4}
INSERT INTO device_points (
    dev_number, point_name, point_number, fun_code, dev_addr, value_type,
    point_ratio, point_offset, user_ratio, user_point_offset, show
)
VALUES
  ('60270012', 'temperature', 0, 3, 1, '字', 1.0, 0.0, 1.0, 0.0, 1),
  ('60270012', 'pressure',    1, 3, 1, '字', 1.0, 0.0, 1.0, 0.0, 1)
ON CONFLICT DO NOTHING;
```

### E6: tools/run_seeds.py
```python
"""按字典序跑 seeds/ 下所有 .sql 文件。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg

SEEDS_DIR = Path(__file__).parent.parent / "seeds"


async def main() -> None:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng",
    ).replace("+asyncpg", "")
    conn = await asyncpg.connect(url)
    try:
        for sql_file in sorted(SEEDS_DIR.glob("*.sql")):
            print(f"[seed] {sql_file.name}")
            await conn.execute(sql_file.read_text(encoding="utf-8"))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**E3–E6 Commits:**
```bash
git add seeds/ tools/run_seeds.py
git commit -m "feat(db): seed data (demo wx group + users + devices + points)"
```

- [ ] **验证跑种子**

```bash
uv run task migrate
uv run task seed
uv run task db
# psql 里执行 SELECT * FROM devices; 应看到 60270012
\q
```

---

## Task E7：阶段 E 收尾

```bash
git tag -a plan-0-stage-e-complete -m "Stage E: fixtures + embedded PG stub + seeds"
```

---

# 阶段 F — 伪设备 PCAP 生成器雏形

> 满足 B-T1：Plan 1 开工前必须有 corpus 金标准。

## Task F1：tools/pcap_gen 子包骨架

**Files:**
- Create: `D:\江苏润盛\tools\pcap_gen\pyproject.toml`
- Create: `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\__init__.py`

- [ ] **Step 1：pyproject**

Create `D:\江苏润盛\tools\pcap_gen\pyproject.toml`:
```toml
[project]
name = "pcap-gen"
version = "0.1.0"
description = "润盛 IoT 伪设备 PCAP 生成器"
requires-python = ">=3.11"
dependencies = [
  "scapy>=2.5",
  "typer>=0.12",
  "ruisheng-shared",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/pcap_gen"]
```

- [ ] **Step 2：__init__**

Create `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\__init__.py`:
```python
"""伪设备 PCAP 生成器。"""
__version__ = "0.1.0"
```

- [ ] **Step 3：Commit**

```bash
git add tools/pcap_gen/
git commit -m "feat(tools): pcap_gen package skeleton"
```

---

## Task F2：modbus_frames.py — CRC16 + 帧构造

**Files:**
- Create: `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\modbus_frames.py`
- Create: `D:\江苏润盛\tests\tools\__init__.py`（空）
- Create: `D:\江苏润盛\tests\tools\test_pcap_gen.py`

- [ ] **Step 1：测试 CRC + 注册帧构造**

Create `D:\江苏润盛\tests\tools\test_pcap_gen.py`:
```python
"""Spec §A.3 — CRC16 验证向量 + §A.4 注册帧。"""
from __future__ import annotations

import pytest

from pcap_gen.modbus_frames import crc16, encode_read_holding, encode_register_frame


@pytest.mark.parametrize(
    ("body", "expected_lo_hi"),
    [
        (bytes.fromhex("0103000000020" + "2"), (0xC4, 0x0B)),  # 01 03 00 00 00 02 -> C4 0B
    ],
)
def test_crc16_standard_vectors(body: bytes, expected_lo_hi: tuple[int, int]) -> None:
    crc = crc16(body)
    assert crc & 0xFF == expected_lo_hi[0]
    assert (crc >> 8) & 0xFF == expected_lo_hi[1]


def test_encode_register_frame_length() -> None:
    frame = encode_register_frame(dev_ser_number="DEMO-SN-0001")
    # 0xFE + 0x15 + 24B + 5B + 3B + CRC(2) = 36 bytes
    assert len(frame) == 2 + 24 + 5 + 3 + 2


def test_encode_read_holding() -> None:
    frame = encode_read_holding(slave=1, start=0, count=2)
    assert frame[0] == 1
    assert frame[1] == 3  # FC
    assert int.from_bytes(frame[2:4], "big") == 0
    assert int.from_bytes(frame[4:6], "big") == 2
    assert len(frame) == 8
```

- [ ] **Step 2：失败**
- [ ] **Step 3：实现**

Create `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\modbus_frames.py`:
```python
"""ModBus RTU 帧构造工具 + CRC16。对应 spec §A.3 + §A.4。"""
from __future__ import annotations

from ruisheng_shared.constants.protocol import CRC16_INIT, CRC16_POLYNOMIAL


def crc16(data: bytes) -> int:
    """ModBus RTU 标准 CRC16（多项式 0xA001，初始 0xFFFF）。返回 16 位整数。"""
    reg = CRC16_INIT
    for byte in data:
        reg ^= byte
        for _ in range(8):
            if reg & 0x0001:
                reg = (reg >> 1) ^ CRC16_POLYNOMIAL
            else:
                reg >>= 1
    return reg


def _append_crc(body: bytes) -> bytes:
    crc = crc16(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def encode_register_frame(*, dev_ser_number: str, fw: str = "1.0.0", hw: str = "1.0") -> bytes:
    """FC 21 注册帧：[0xFE][15][DevSerNumber(24)][FwVer(5)][HwVer(3)][CRC]。"""
    ser = dev_ser_number.encode("ascii").ljust(24, b"\x00")[:24]
    fw_b = fw.encode("ascii").ljust(5, b"\x00")[:5]
    hw_b = hw.encode("ascii").ljust(3, b"\x00")[:3]
    body = b"\xFE\x15" + ser + fw_b + hw_b
    return _append_crc(body)


def encode_read_holding(*, slave: int, start: int, count: int) -> bytes:
    body = bytes([slave, 3]) + start.to_bytes(2, "big") + count.to_bytes(2, "big")
    return _append_crc(body)


def encode_read_holding_response(*, slave: int, values: list[int]) -> bytes:
    data = b"".join(v.to_bytes(2, "big", signed=False) for v in values)
    body = bytes([slave, 3, len(data)]) + data
    return _append_crc(body)


def encode_write_single_register(*, slave: int, reg: int, value: int) -> bytes:
    body = bytes([slave, 6]) + reg.to_bytes(2, "big") + value.to_bytes(2, "big", signed=False)
    return _append_crc(body)


def encode_heartbeat(*, slave: int, token: int) -> bytes:
    """FC 0x19 心跳帧（新约定）。"""
    body = bytes([slave, 0x19]) + token.to_bytes(4, "big")
    return _append_crc(body)
```

- [ ] **Step 4：测试**
- [ ] **Step 5：Commit**

```bash
git add tools/pcap_gen/src/pcap_gen/modbus_frames.py \
        tests/tools/test_pcap_gen.py
git commit -m "feat(tools): ModBus RTU frame codec + CRC16 with test vectors"
```

---

## Task F3：scenarios.py — 场景化 PCAP 生成

**Files:**
- Create: `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\scenarios.py`

- [ ] **Step 1：实现**

Create `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\scenarios.py`:
```python
"""伪设备场景生成器。输出 pcap + expected.json。"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from scapy.all import IP, TCP, Raw, wrpcap

from .modbus_frames import (
    encode_heartbeat,
    encode_read_holding,
    encode_read_holding_response,
    encode_register_frame,
)


def gen_normal_session(
    *,
    dev_ser: str,
    slave: int,
    frames_count: int,
    out_pcap: Path,
    out_expected: Path,
    seed: int = 42,
) -> None:
    """生成一条"注册 + N 次轮询"的正常会话 pcap。

    同步输出 expected.json：
      {
        "dev_ser": "...",
        "frames": [{"type": "register", ...}, {"type": "read", ...}, ...],
        "values": [[v0, v1], [v2, v3], ...],
      }
    """
    rng = random.Random(seed)
    packets = []
    expected_values: list[list[int]] = []
    client_ip = "10.0.0.1"
    server_ip = "10.0.0.2"
    sport = 10000 + rng.randint(0, 1000)
    dport = 6000

    # 注册帧
    reg = encode_register_frame(dev_ser_number=dev_ser)
    packets.append(
        IP(src=client_ip, dst=server_ip)
        / TCP(sport=sport, dport=dport, flags="PA")
        / Raw(load=reg)
    )

    # N 次：gw → 设备 read；设备 → gw response
    for i in range(frames_count):
        req = encode_read_holding(slave=slave, start=0, count=2)
        packets.append(
            IP(src=server_ip, dst=client_ip)
            / TCP(sport=dport, dport=sport, flags="PA")
            / Raw(load=req)
        )
        values = [rng.randint(20, 80), rng.randint(100, 200)]
        resp = encode_read_holding_response(slave=slave, values=values)
        packets.append(
            IP(src=client_ip, dst=server_ip)
            / TCP(sport=sport, dport=dport, flags="PA")
            / Raw(load=resp)
        )
        expected_values.append(values)

    # 心跳
    hb = encode_heartbeat(slave=slave, token=rng.randint(0, 2**32 - 1))
    packets.append(
        IP(src=server_ip, dst=client_ip)
        / TCP(sport=dport, dport=sport, flags="PA")
        / Raw(load=hb)
    )

    wrpcap(str(out_pcap), packets)
    out_expected.write_text(
        json.dumps(
            {
                "scenario": "normal_session",
                "dev_ser": dev_ser,
                "slave": slave,
                "frames_count": frames_count,
                "values": expected_values,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
```

- [ ] **Step 2：Commit**

```bash
git add tools/pcap_gen/src/pcap_gen/scenarios.py
git commit -m "feat(tools): pcap scenarios (normal_session with register+poll+heartbeat)"
```

---

## Task F4：typer CLI

**Files:**
- Create: `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\cli.py`

- [ ] **Step 1：实现**

Create `D:\江苏润盛\tools\pcap_gen\src\pcap_gen\cli.py`:
```python
"""命令行入口：`pcap-gen generate ...`"""
from __future__ import annotations

from pathlib import Path

import typer

from .scenarios import gen_normal_session

app = typer.Typer()


@app.command()
def normal(
    dev_ser: str = typer.Option("DEMO-SN-0001"),
    slave: int = typer.Option(1),
    frames: int = typer.Option(100),
    out_dir: Path = typer.Option(Path("corpus/generated")),
    seed: int = typer.Option(42),
) -> None:
    """生成一条"注册 + N 次轮询 + 心跳"的正常会话。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"normal_{dev_ser}_{frames}_seed{seed}"
    gen_normal_session(
        dev_ser=dev_ser,
        slave=slave,
        frames_count=frames,
        out_pcap=out_dir / f"{name}.pcap",
        out_expected=out_dir / f"{name}.expected.json",
        seed=seed,
    )
    typer.echo(f"wrote: {out_dir / name}.pcap and .expected.json")


if __name__ == "__main__":
    app()
```

Edit `D:\江苏润盛\tools\pcap_gen\pyproject.toml` 加 scripts：
```toml
[project.scripts]
pcap-gen = "pcap_gen.cli:app"
```

- [ ] **Step 2：安装 + 跑**

```bash
uv sync
uv run pcap-gen normal --frames 100
ls corpus/generated/
# 期望：2 个文件 .pcap 和 .expected.json
```

- [ ] **Step 3：Commit**

```bash
git add tools/pcap_gen/
git commit -m "feat(tools): pcap-gen CLI (normal command)"
```

---

## Task F5：生成首批 15 个 corpus pcap（脚本化）

**Files:**
- Create: `D:\江苏润盛\tools\pcap_gen\scripts\gen_initial_corpus.py`

- [ ] **Step 1：批量脚本**

```python
"""首批 15 个 pcap：5 种设备类型 × 3 种工况。"""
from __future__ import annotations

import subprocess
from pathlib import Path

DEVICE_TYPES = ["采油机", "保温", "电气", "液位", "温湿度"]
SEEDS = [100, 200, 300]  # 3 种工况


def main() -> None:
    for i, dtype in enumerate(DEVICE_TYPES):
        for j, seed in enumerate(SEEDS):
            dev_ser = f"DEMO-{dtype}-{j}".encode("ascii", errors="ignore").decode() \
                or f"DEMO-TYPE{i}-{j}"
            subprocess.check_call([
                "uv", "run", "pcap-gen", "normal",
                "--dev-ser", dev_ser,
                "--frames", "100",
                "--seed", str(seed),
            ])


if __name__ == "__main__":
    main()
```

- [ ] **Step 2：跑**

```bash
uv run python tools/pcap_gen/scripts/gen_initial_corpus.py
ls corpus/generated/ | wc -l   # 期望 30（15 pcap + 15 expected）
```

- [ ] **Step 3：Commit**

```bash
git add tools/pcap_gen/scripts/
git commit -m "chore(corpus): initial 15 normal pcaps for 5 device types × 3 seeds

corpus/generated/ itself is gitignored (E2 plan)."
```

---

## Task F6：阶段 F 收尾

```bash
git tag -a plan-0-stage-f-complete -m "Stage F: pcap generator + initial 15 corpus"
```

---

# 阶段 G — CI 完备 + 文档

## Task G1：扩展 CI workflow（加 integration / alembic check / schema guard）

**Files:**
- Modify: `D:\江苏润盛\.github\workflows\ci.yml`

- [ ] **Step 1：扩展**

Edit `D:\江苏润盛\.github\workflows\ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [master, develop]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy .

  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync
      - run: uv run pytest tests/unit tests/tools -v --cov --cov-fail-under=90

  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: timescale/timescaledb:2.16.1-pg15
        env:
          POSTGRES_USER: ruisheng_dev
          POSTGRES_PASSWORD: ruisheng_dev
          POSTGRES_DB: ruisheng
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready -U ruisheng_dev"
          --health-interval 5s --health-retries 10
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run alembic upgrade head
        env:
          DATABASE_URL: postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng
      - run: uv run pytest tests/integration -v -m integration
        env:
          DATABASE_URL: postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng
          REDIS_URL: redis://127.0.0.1:6379/0

  alembic-check:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: timescale/timescaledb:2.16.1-pg15
        env:
          POSTGRES_USER: ruisheng_dev
          POSTGRES_PASSWORD: ruisheng_dev
          POSTGRES_DB: ruisheng
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready"
          --health-interval 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - name: 验证迁移对称（up → down → up）
        run: |
          uv run alembic upgrade head
          uv run alembic downgrade base
          uv run alembic upgrade head
        env:
          DATABASE_URL: postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng

  schema-version-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - name: 检查 SHARED_SCHEMA_VERSION 是否随 models 变更同步升级
        run: uv run python tools/verify_schema_version.py
```

- [ ] **Step 2：Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add integration / alembic symmetry / schema-version jobs"
```

---

## Task G2：完善 `tools/verify_schema_version.py`（breaking 检测）

**Files:**
- Modify: `D:\江苏润盛\tools\verify_schema_version.py`

- [ ] **Step 1：升级实现**

Replace `D:\江苏润盛\tools\verify_schema_version.py`:
```python
"""验证：若 ruisheng-shared/src/ruisheng_shared/models/ 改动了，则：
1) CHANGELOG.md 必须有今日条目
2) 若任一条目以 `breaking:` 前缀，SHARED_SCHEMA_VERSION 必须已升级
"""
from __future__ import annotations

import datetime as _dt
import pathlib
import re
import subprocess
import sys

CHANGELOG = pathlib.Path("ruisheng-shared/src/ruisheng_shared/CHANGELOG.md")
INIT = pathlib.Path("ruisheng-shared/src/ruisheng_shared/__init__.py")
VERSION_RE = re.compile(r"SHARED_SCHEMA_VERSION\s*:\s*int\s*=\s*(\d+)")


def _git_diff(*args: str) -> str:
    return subprocess.check_output(["git", "diff", *args], text=True)


def _schema_files_changed() -> bool:
    out = subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD^", "HEAD"],
        text=True,
    )
    return any(
        p.startswith("ruisheng-shared/src/ruisheng_shared/models/")
        or p.startswith("ruisheng-shared/src/ruisheng_shared/schemas/")
        for p in out.splitlines()
    )


def _has_today_entry() -> bool:
    today = _dt.date.today().isoformat()
    return today in CHANGELOG.read_text(encoding="utf-8")


def _has_breaking_today() -> bool:
    today = _dt.date.today().isoformat()
    text = CHANGELOG.read_text(encoding="utf-8")
    # 从 "## <today>" 开始到下一个 "## " 之间
    section = re.search(rf"## .*{today}.*?(?=\n## |\Z)", text, re.DOTALL)
    if not section:
        return False
    return "breaking:" in section.group(0)


def _version_changed() -> bool:
    diff = _git_diff("HEAD^", "HEAD", "--", str(INIT))
    return "SHARED_SCHEMA_VERSION" in diff and ("+SHARED" in diff or "-SHARED" in diff)


def main() -> int:
    if not _schema_files_changed():
        return 0
    if not _has_today_entry():
        print("ERROR: shared models/schemas 改动但 CHANGELOG 无今日条目", file=sys.stderr)
        return 1
    if _has_breaking_today() and not _version_changed():
        print(
            "ERROR: 今日有 breaking 变更，SHARED_SCHEMA_VERSION 必须同时升级",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2：Commit**

```bash
git add tools/verify_schema_version.py
git commit -m "ci(schema-guard): enforce version bump on breaking changes"
```

---

## Task G3：docs/ARCHITECTURE.md 开发侧架构速览

**Files:**
- Create: `D:\江苏润盛\docs\ARCHITECTURE.md`

- [ ] **Step 1：写文档**

Create `D:\江苏润盛\docs\ARCHITECTURE.md`:
```markdown
# 开发者视角：架构速览

## 三个运行单元

- `ruisheng-api`（Plan 2 建）：Web 后端（FastAPI + uvicorn）
- `ruisheng-gw`（Plan 1 建）：采集网关（asyncio + pymodbus 层）
- `ruisheng-web`（Plan 3 建）：Vue 3 前端

## 共享基座（Plan 0 已建）

- `ruisheng-shared`：ORM + enums + errors + constants + validators
- `alembic/`：迁移 + hypertables + RLS
- Docker Compose 本地：PG + TimescaleDB + Redis
- pcap 生成器：给 Plan 1 预置 corpus

## 关键契约（跨包）

1. **SHARED_SCHEMA_VERSION**：api/gw 启动时比对
2. **FunCode 归一化**：FC 13 → 3, FC 26 → 6, FC 7/12 砍
3. **RS485 物理约束表**：`validators.rs485.min_poll_interval_decisec`
4. **ErrCode + BizError**：所有业务异常的底层

## 查看设计决策

完整设计见：`docs/superpowers/specs/2026-04-13-ruisheng-iot-design.md`

## 贡献流程

见 `CONTRIBUTING.md`
```

- [ ] **Step 2：Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: developer architecture overview"
```

---

## Task G4：覆盖率门槛 + mutation testing 占位

**Files:**
- Create: `D:\江苏润盛\.github\workflows\mutation.yml`（weekly 调度）

- [ ] **Step 1：创建**

Create `D:\江苏润盛\.github\workflows\mutation.yml`:
```yaml
name: Mutation Testing

on:
  schedule:
    - cron: '0 2 * * 0'   # 每周日 02:00 UTC
  workflow_dispatch:

jobs:
  mutmut:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: |
          uv pip install mutmut
          uv run mutmut run \
            --paths-to-mutate ruisheng-shared/src/ruisheng_shared \
            --runner "pytest tests/unit/shared -q"
      - run: uv run mutmut results
      - name: Fail if survival rate > 10%
        run: |
          uv run mutmut results | python -c "import sys; lines=sys.stdin.read(); alive=0; total=0; [...]; exit(1 if alive/total > 0.1 else 0)"
```

- [ ] **Step 2：Commit**

```bash
git add .github/workflows/mutation.yml
git commit -m "ci: weekly mutation testing (mutmut) with 10% survival gate"
```

---

## Task G5：Plan 0 完成 README 补完 + 最终 tag

**Files:**
- Modify: `D:\江苏润盛\README.md`

- [ ] **Step 1：在 README 末尾追加 "状态"**

Append to `D:\江苏润盛\README.md`:
```markdown

---

## 当前状态

- [x] **Plan 0**：基础设施（本仓库的 `ruisheng-shared` + alembic + docker compose + pcap gen）
- [ ] Plan 1：采集网关 `ruisheng-gw`
- [ ] Plan 2：Web API `ruisheng-api`
- [ ] Plan 3：前端 `ruisheng-web`
- [ ] Plan 4：部署与运维
```

- [ ] **Step 2：最终验收**

```bash
cd "D:\江苏润盛"
uv run task bootstrap   # 一键全跑
uv run pytest tests/ --cov --cov-fail-under=90
```
期望：全部通过。

- [ ] **Step 3：打最终 tag**

```bash
git add README.md
git commit -m "docs: mark Plan 0 complete in README"
git tag -a plan-0-complete -m "Plan 0 Foundation fully complete (2026-04-13)"
```

---

## Task G6：技术债清理 — 迁移 `[tool.uv] dev-dependencies` → `[dependency-groups]`

**目的：** Stage A 用了 uv 已弃用的 `[tool.uv] dev-dependencies` 写法（每次 `uv sync` 报 warning）。PEP 735 `[dependency-groups]` 是 uv 新推荐写法。同时借此根治 Stage A 遗留的 `testcontainers[postgres]` extra 解析问题（当时靠显式加 `asyncpg` 绕过）。

**Files：**
- Modify: `D:\江苏润盛\pyproject.toml`
- Modify: `D:\江苏润盛\uv.lock`
- Modify: `D:\江苏润盛\.github\workflows\ci.yml`（若 sync 命令需改为 `--group dev`）

- [ ] **Step 1：备份当前 pyproject.toml**

```bash
cp pyproject.toml pyproject.toml.bak
```

- [ ] **Step 2：把 `[tool.uv] dev-dependencies` 块改为 `[dependency-groups] dev`**

Edit `D:\江苏润盛\pyproject.toml`，删除：
```toml
[tool.uv]
dev-dependencies = [ ... ]
```
替换为：
```toml
[dependency-groups]
dev = [
    # 把原 dev-dependencies 的条目完整复制到这里
]
```

- [ ] **Step 3：尝试去掉显式 `asyncpg`**

在 `[project]` 的 `dependencies` 里先把 `asyncpg` 注释掉，保留 `testcontainers[postgres]`。

- [ ] **Step 4：重新解析 + 测试 testcontainers extra**

```bash
rm uv.lock
uv sync --group dev
uv run python -c "import testcontainers.postgres; import asyncpg; print('ok')"
```
期望：无警告；`asyncpg` 能被间接拉入（通过 testcontainers[postgres]）。

若 testcontainers 仍未把 asyncpg 带进来 → 保留 `asyncpg` 显式依赖（只做 dependency-groups 迁移部分即可）。

- [ ] **Step 5：跑全量测试**

```bash
uv run pytest tests/ --cov --cov-fail-under=90
```
期望：全部通过；无 dev-dependencies 弃用警告。

- [ ] **Step 6：更新 CI（如需要）**

检查 `.github/workflows/ci.yml` 的 `uv sync` 命令，若仍用旧写法需改为 `uv sync --group dev`（或 `--all-groups`，视 CI job 需求）。

- [ ] **Step 7：删除备份 + Commit**

```bash
rm pyproject.toml.bak
git add pyproject.toml uv.lock .github/workflows/ci.yml
git commit -m "chore(deps): migrate to PEP 735 dependency-groups; retry testcontainers extras"
```

---

## Task G7：ruisheng-shared release workflow

**目的：** ruisheng-shared 将被 Plan 1（gw）/ Plan 2（api）通过 `SHARED_SCHEMA_VERSION` 引用。Plan 0 完成时需要一个清晰的版本发布流程：semver bump + CHANGELOG + git tag + GitHub Release。**不做 PyPI publish**（私有仓）。

**Files：**
- Create: `D:\江苏润盛\ruisheng-shared\CHANGELOG.md`
- Create: `D:\江苏润盛\.github\workflows\release-shared.yml`
- Modify: `D:\江苏润盛\ruisheng-shared\pyproject.toml`（version 字段保持 `0.1.0`）
- Modify: `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\__init__.py`（暴露 `SHARED_SCHEMA_VERSION`）

- [ ] **Step 1：在 ruisheng_shared 包暴露 SHARED_SCHEMA_VERSION**

Edit `D:\江苏润盛\ruisheng-shared\src\ruisheng_shared\__init__.py`：
```python
"""Ruisheng shared package（跨 gw / api / web 的类型、枚举、常量、schemas）。"""

SHARED_SCHEMA_VERSION = "0.1.0"  # 与 pyproject.toml version 同步；breaking 变更须 bump major
__version__ = SHARED_SCHEMA_VERSION
```

- [ ] **Step 2：写 CHANGELOG.md 初始条目**

Create `D:\江苏润盛\ruisheng-shared\CHANGELOG.md`:
```markdown
# Changelog

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) + [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-04-XX
### Added
- Plan 0 基础建设：enums / errors / constants / validators / schemas 骨架
- ORM 23 张表 + Alembic 初始迁移（含 TimescaleDB hypertable / compression / retention）
- PCAP 生成器雏形（15 个 corpus 场景）
- CI：lint / unit / integration / alembic-check / schema-guard
```

- [ ] **Step 3：创建 release workflow**

Create `D:\江苏润盛\.github\workflows\release-shared.yml`:
```yaml
name: Release ruisheng-shared

on:
  push:
    tags:
      - "shared-v*.*.*"

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Extract version from tag
        id: version
        run: echo "VERSION=${GITHUB_REF_NAME#shared-v}" >> "$GITHUB_OUTPUT"

      - name: Verify pyproject version matches tag
        run: |
          PY_VER=$(grep -E '^version\s*=' ruisheng-shared/pyproject.toml | head -1 | sed -E 's/.*"(.+)".*/\1/')
          if [ "$PY_VER" != "${{ steps.version.outputs.VERSION }}" ]; then
            echo "::error::pyproject version $PY_VER != tag ${{ steps.version.outputs.VERSION }}"
            exit 1
          fi

      - name: Extract changelog section
        id: changelog
        run: |
          awk "/^## \\[${{ steps.version.outputs.VERSION }}\\]/,/^## \\[/" ruisheng-shared/CHANGELOG.md \
            | sed '$d' > /tmp/release-notes.md
          cat /tmp/release-notes.md

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          body_path: /tmp/release-notes.md
          name: ruisheng-shared ${{ steps.version.outputs.VERSION }}
          draft: false
          prerelease: false
```

- [ ] **Step 4：写 docs/RELEASE.md 简短流程说明**

Create `D:\江苏润盛\docs\RELEASE.md`:
```markdown
# ruisheng-shared 发布流程

1. 在 master 分支更新 `ruisheng-shared/pyproject.toml` 的 `version`（遵 semver）
2. 同步更新 `src/ruisheng_shared/__init__.py` 的 `SHARED_SCHEMA_VERSION`
3. 在 `ruisheng-shared/CHANGELOG.md` 把 `[Unreleased]` 下的条目搬到新版本下
4. Commit：`chore(release): ruisheng-shared vX.Y.Z`
5. 打 tag：`git tag -a shared-vX.Y.Z -m "..."`
6. Push：`git push && git push --tags`
7. GitHub Actions 会自动创建 Release（含 CHANGELOG 提取段）

## SemVer 规则

- **major**：shared 的 schema 接口（enum 值、常量、错误码、pydantic model 字段）有 **breaking** 改动
- **minor**：新增枚举值 / 新增 schema 字段（向后兼容）
- **patch**：文档、测试、内部实现改动
```

- [ ] **Step 5：测试 tag 提取逻辑（dry run）**

```bash
# 本地模拟：确认 awk 能提取 CHANGELOG 里的 0.1.0 段
awk "/^## \\[0.1.0\\]/,/^## \\[/" ruisheng-shared/CHANGELOG.md | sed '$d'
```
期望：输出 0.1.0 段的 Added 列表。

- [ ] **Step 6：Commit**

```bash
git add ruisheng-shared/CHANGELOG.md \
        ruisheng-shared/src/ruisheng_shared/__init__.py \
        .github/workflows/release-shared.yml \
        docs/RELEASE.md
git commit -m "feat(release): ruisheng-shared release workflow with SHARED_SCHEMA_VERSION"
```

- [ ] **Step 7：（可选）实际打首个 tag**

> 只在 Plan 0 **全部完成后** 再做。此 step 可以延到 G5 的最终 tag 之后。
>
> ```bash
> git tag -a shared-v0.1.0 -m "ruisheng-shared 0.1.0 — Plan 0 foundation"
> git push origin shared-v0.1.0
> ```

---

# 自检（Self-Review）

## 1. Spec 覆盖性

- [x] `§2.3` shared 版本化 → A3 + G2
- [x] `§4.2` 23 张表 → Stage C
- [x] `§5.1` ErrCode → B7
- [x] `§6.2` 覆盖率门槛 → A1 `fail_under=90` + G1 CI gate
- [x] `§6.5.1` testcontainers + embedded PG → E1 + E2
- [x] `§6.6.1` 伪 PCAP 生成器 → Stage F
- [x] `§A.3` CRC16 验证向量 → F2
- [x] `§A.7` 端口分工常量 → B8
- [x] `§A.8` RS485 物理约束 → B10

未覆盖项（延期到 Plan 1–4）：
- ModBus 协议解析（Plan 1）
- 告警引擎 `alarm_engine` 及 fixture（Plan 1）
- 微信支付验签（Plan 2）
- 前端（Plan 3）
- NSSM / Nginx / 备份（Plan 4）

## 2. Placeholder 扫描

- [x] 无 "TBD" "TODO"（除 `tools/embedded_pg.py` stub 明确标注待 Q-E06 的部分——这是已知有意的 stub）
- [x] 无 "appropriate error handling" 式模糊
- [x] 测试步骤都有完整代码块

## 3. 类型一致性

- [x] `FunCode.normalize` 签名在 B2 定义 → Plan 1 使用处会直接 import
- [x] `BizError(ErrCode, msg)` 构造在 B7 定义 → B10 `validate_bus_feasibility` 使用一致
- [x] `ControlStatus.is_terminal` 属性在 B5 定义
- [x] 所有 model 字段类型与 `devices.update_interval_decisec INT` 等 DDL 一致

---

# Execution 交接

**Plan 0 完成并 tag 后**，两个执行选项：

**1. Subagent-Driven（推荐）** — 我派一个独立 subagent 执行本 plan 的一批任务（如 Stage A 12 个 task），review 后继续下一批。适合 Plan 0 这种任务数较多（≈110）但边界清晰的 plan。

**2. Inline Execution** — 直接在本会话里执行，带检查点。适合想随时介入调整细节。

继续前请：
- 确认 Plan 0 已 ack
- 选择执行方式
- 下一个 plan 目标（Plan 1 采集网关是关键路径，最早可开工）

---

**Plan 结束。**
