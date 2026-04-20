# 贡献指南

## Windows 开发环境注意事项

本项目路径含中文字符（`D:\江苏润盛\`），在 Windows 上有几个必须设置的环境项：

1. **Docker Desktop**：`task up` / `migrate` / D6 集成测试都依赖容器化的 PostgreSQL+TimescaleDB+Redis。从 https://www.docker.com/products/docker-desktop 安装。
2. **Git for Windows 的 `usr/bin` 在 PATH 里**：pre-commit 的 shell hook 需要 `/bin/bash` 和标准 Unix 工具。若未设置，`pre-commit install` 写出的 hook 文件在执行时会报"`pre-commit` not found"。推荐在系统 PATH 前部添加：
   ```
   C:\Program Files\Git\usr\bin
   ```
3. **控制台 UTF-8**：Windows 默认 GBK 编码会导致中文路径被错误编码到 hook 文件中。在所有终端会话里启用 UTF-8：
   ```powershell
   chcp 65001
   # 或永久设置：
   $env:PYTHONUTF8 = "1"   # PowerShell 用户配置
   ```

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

**另外**：`uv sync` 默认**不**安装 workspace 成员包（ruisheng-shared 等）。请总是用：
```bash
uv sync --all-packages
```

## 测试要求

- 新代码必须带测试
- `protocol/` 分支覆盖 95%，`domain/` 90%，`services/` 80%（见设计文档 §6.2）
- mutmut 每周自动跑（存活率 < 10% 算有效）

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
