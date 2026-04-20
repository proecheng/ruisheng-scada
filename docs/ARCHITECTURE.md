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

## 设备接入方式对比

| 接入方式 | 适用场景 | 配置方式 | 设备识别 | 连接发起方 |
|---------|---------|---------|---------|---------|
| TCP/DTU | 远程设备，4G 网络 | 默认，无需配置 | 心跳帧中的序列号 | 设备主动连服务器 |
| RS485 串口 | 本地设备，串口线直连 | `GW_SERIAL_PORTS=[{"port":"COM3","baud_rate":9600}]` | DB 中 `modbus_addr` + `serial_port` | 服务器开串口，静态绑定 |

两种方式可同时启用，共享同一套 SessionMap / poller / BatchWriter 逻辑。

## 查看设计决策

完整设计见：`docs/superpowers/specs/2026-04-13-ruisheng-iot-design.md`

## 贡献流程

见 `CONTRIBUTING.md`
