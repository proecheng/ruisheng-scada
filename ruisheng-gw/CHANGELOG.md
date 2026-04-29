# Changelog (ruisheng-gw)

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) + [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.2] - 2026-04-29
### Fixed
- pytest 全量收集在 monorepo 多个 `tests` 目录下不再发生 conftest / 同名测试模块冲突。
- 缺少 Docker、dev PostgreSQL 或测试角色密码时，集成测试明确 skip，不再以环境异常失败。
- `RealClock.monotonic()` 改用 `time.perf_counter()`，提升 Windows 短间隔计时测试稳定性。

### Changed
- tenant-filter lint suppression 使用 Ruff 可识别的 `TNL001` 外部规则码，并保留旧写法兼容。
- CI tenant-filter lint 改用脚本路径执行，避免依赖 workspace 包导入路径。

## [0.1.1] - 2026-04-21
### Fixed
- `main()` 正式接入 `run_server()`：Docker 容器不再因进程退出而持续重启
- health 端点（`:9090`）在 `run_server()` 启动时自动开启，DB alembic 校验通过后标记 `db_ok=True`
- structlog JSON 输出接入 `run_server()` 启动日志

### Changed
- `EXPECTED_ALEMBIC_HEAD` 升至 `0009_serial_port_unique`（需运行新迁移后才能启动）

### Migration
- `0009_serial_port_unique`：对 `transport_type = 'serial'` 设备加 `(serial_port, modbus_addr)` 部分唯一索引，防止同一串口地址重复注册

## [0.1.0] - 2026-04-19
### Added
- 设备采集网关 MVP：asyncio TCP server + ModBus RTU-on-TCP 协议 10 FunCode
- 长度感知 framer（非 CRC-boundary）+ DTU 厂商 heartbeat stripper + idle-timeout 重同步
- FC 3/5/6/16/19/20/21/22/100 编解码 + ExceptionResponse（fc|0x80）分派
- FC 13/26 私有帧 BLOCKED 占位（Plan 1.5 加 vendor 解码）
- Per-device 轮询协程 + asyncio.create_task + add_done_callback 手动 supervise
- Per-RS485-bus `asyncio.Lock` 序列化 + 15s acquire timeout
- Clock 协议注入（RealClock + FakeClock 确定性测试）
- batch_writer 双阈（100ms timer + 500 行）+ drop-tail + retry 3× 指数退避
- WAL 兜底：`{wal_dir}/YYYYMMDD-HHMM.ndjson` + 1GB rotate + 10GB drop 最老 + 启动重放
- Redis realtime pub (`channel:realtime:v1:{dev_number}`) + simple alarm pub (`channel:alarm:v1:{dev_number}`)
- threshold alarm（无状态机，Plan 1.5 加 fired/reset 配对）
- `/health` + `/ready` + `/metrics`（Prometheus 文本格式）on :9090
- structlog JSON + correlation context vars（conn_id / dev_number / bus_id / usr_group）
- CI lint `tenant_filter_lint`：FORCE RLS 12 表强制 `usr_group` 谓词
- 104 unit + 1 contract integration + 1 benchmark 测试
- coverage line 85% + branch 75% 双门槛

依赖：ruisheng-shared 0.1.0。Plan 2 api 可通过 `from ruisheng_gw.pubsub.schemas import RealtimeEvent, AlarmEvent` 消费契约。
