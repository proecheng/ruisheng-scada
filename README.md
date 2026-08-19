# 江苏润盛 IoT SCADA 平台

> 工业 RS485/Modbus 设备远程监控平台。基于 Python FastAPI + Vue 3 + PostgreSQL/TimescaleDB + Redis，支持 TCP/DTU 和 RS485 串口双模式设备接入。

## Releases

| 组件 | 版本 | 说明 |
|------|------|------|
| 生产部署包 | [deploy-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/deploy-v0.1.0) | Docker Compose 全栈部署 |
| 前端 | [web-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/web-v0.1.0) | Vue 3 SPA |
| API | [api-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/api-v0.1.0) | FastAPI REST + WebSocket |
| 网关 | [gw-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/gw-v0.1.0) | Modbus 采集网关 |

## 快速部署（Docker）

**前提：** 已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
# 1. 配置环境变量
cp .env.prod.example .env.prod
# 编辑 .env.prod，填写所有 CHANGE_ME_* 密码

# 2. 初始化数据库（不启动 API、GW 或 Web 对外服务）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d postgres redis migrate

# 3. 检查迁移任务成功退出
docker compose -f docker-compose.prod.yml --env-file .env.prod ps --all migrate
```

生产 bootstrap 只迁移数据库表结构，不创建演示数据或账号。管理员引导和凭据交接尚未交付，B-02 不解除 G0-05/CAP-2；在独立流程获批并完成前，不得将系统开放给用户。

详细说明见 [`deploy/setup-customer.md`](deploy/setup-customer.md)。

## 本地开发

```bash
# 安装 uv
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 同步依赖
uv sync --all-packages

# 启动 PostgreSQL + Redis
uv run task up

# 数据库迁移
uv run task migrate

# 显式加载演示数据（仅限本地开发/测试，生产环境不得运行）
uv run task seed

# 运行测试（250 unit tests）
uv run task test

# 启动前端开发服务器
cd ruisheng-web && pnpm install && pnpm dev
```

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vite + Pinia + ECharts + vue-konva |
| API | Python FastAPI + uvicorn + SQLAlchemy async |
| 网关 | Python asyncio（Modbus TCP/RTU + RS485 串口） |
| 数据库 | PostgreSQL 15 + TimescaleDB 2.16（时序数据） |
| 缓存/消息 | Redis 7（pub/sub + 限流 + JWT 黑名单） |
| 部署 | Docker Compose（6 services） |

## 仓库结构

```
ruisheng-scada/
├── ruisheng-shared/        # 共享包：ORM 模型 + 错误码 + 常量
├── ruisheng-api/           # FastAPI REST + WebSocket API
│   └── Dockerfile
├── ruisheng-gw/            # Modbus 采集网关（TCP + RS485）
│   └── Dockerfile
├── ruisheng-web/           # Vue 3 前端 SPA
│   ├── Dockerfile
│   └── nginx.conf
├── alembic/                # 数据库迁移（8 个版本）
├── seeds/                  # 本地开发/测试演示数据（显式加载）
├── scripts/
│   └── entrypoint-migrate.sh
├── deploy/                 # 客户机部署包
│   ├── export-images.sh
│   └── setup-customer.md
├── docker-compose.prod.yml # 生产部署
├── docker-compose.dev.yml  # 本地开发（仅 DB）
└── .env.prod.example       # 环境变量模板
```

## 开发进度

- [x] **Plan 0**：基础设施（共享包 + alembic + docker + 工具链）
- [x] **Plan 1**：采集网关 `ruisheng-gw`（Modbus TCP/RTU + WAL + Redis pub/sub）
- [x] **Plan 2**：Web API `ruisheng-api`（250+ 端点 + JWT + RLS + WebSocket）
- [x] **Plan 3**：前端 `ruisheng-web`（Vue 3 SPA + 组态画面 + ECharts + PWA）
- [x] **Serial Port**：RS485 串口设备接入（双模式）
- [x] **Plan 4**：Docker Compose 生产部署（本机冒烟测试通过）
- [ ] **Plan 5**：客户机实际部署验证 + GW 串口真机测试

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
