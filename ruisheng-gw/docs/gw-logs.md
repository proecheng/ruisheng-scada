# gw 日志 schema

所有日志走 structlog JSON stdout，每行一个 JSON object。

## 必带字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp` | ISO8601 | 事件时间 |
| `level` | string | debug/info/warning/error |
| `event` | string | 事件名（见下表） |

## Context vars（随 `bind_context` 自动注入）

| 字段 | 使用场景 |
|---|---|
| `conn_id` | 每 TCP 连接唯一 ID（uuid 前 8 位） |
| `dev_number` | 设备编号 |
| `bus_id` | `{dtu_ip}:{dtu_port}` |
| `usr_group` | 租户编号 |

## 常见 event 名

| event | level | 触发 |
|---|---|---|
| `connection_accept` | info | 新 DTU 连接 |
| `connection_close` | info | TCP 断 |
| `crc_mismatch` | warning | CRC 失败，丢帧 |
| `modbus_exception` | warning | 设备返回异常码 |
| `heartbeat_timeout` | warning | 3× 心跳超时 |
| `poller_crash` | error | 协程异常 |
| `quarantine_device` | error | restart 超限 |
| `batch_flush` | debug | 批量写 DB 成功 |
| `wal_write` | warning | DB 失败 WAL fallback |
