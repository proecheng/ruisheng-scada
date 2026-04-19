# 江苏润盛 IoT SCADA 平台（重做版）

> 工业 RS485/ModBus 设备远程监控平台的重做版。基于 Python FastAPI + Vue 3 + PostgreSQL+TimescaleDB + Redis。

## 快速开始

1. 安装 uv（Python 包管理器）：
   ```powershell
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. 同步依赖：
   ```bash
   uv sync --all-packages
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
ruisheng-scada/
├── ruisheng-shared/      # 共享包：ORM + enums + errors + constants
├── alembic/              # 数据库迁移（Stage D 建立）
├── seeds/                # SQL 种子数据（Stage E 建立）
├── tools/
│   ├── pcap_gen/         # 伪设备 PCAP 生成器（Stage F 建立）
│   ├── embedded_pg.py    # Windows 内嵌 PG 包装（Stage E 建立）
│   └── verify_schema_version.py
├── tests/
├── docs/
│   └── superpowers/
│       ├── specs/        # 设计文档
│       └── plans/        # 实施计划
└── docker-compose.dev.yml
```

## 当前状态

- [x] **Plan 0**：基础设施（`ruisheng-shared` + alembic + docker compose + pcap gen）
- [x] **Plan 1**：采集网关 `ruisheng-gw`
- [ ] **Plan 2**：Web API `ruisheng-api`
- [ ] **Plan 3**：前端 `ruisheng-web`
- [ ] **Plan 4**：部署与运维

## 贡献

见 `CONTRIBUTING.md`。
