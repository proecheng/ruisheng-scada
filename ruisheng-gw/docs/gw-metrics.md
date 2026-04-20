# gw Prometheus metrics 清单

暴露在 `:{GW_HEALTH_PORT=9090}/metrics`。

## Counters

| 名 | Labels | 说明 |
|---|---|---|
| `ruisheng_gw_crc_error_total` | `dev_number` | CRC 失败帧数 |
| `ruisheng_gw_framer_resync_total` | — | framer idle-timeout 重同步次数 |
| `ruisheng_gw_framing_error_total` | — | 连续 10+ parse-fail 触发断连次数 |
| `ruisheng_gw_modbus_exception_total` | `dev_number, code` | 设备返回异常码统计 |
| `ruisheng_gw_heartbeat_timeout_total` | — | FC 0x19 超时计数 |
| `ruisheng_gw_writer_stale_total` | `dev_number` | poller 发现 writer 已失效 |
| `ruisheng_gw_poller_restart_total` | `dev_number, reason` | supervisor 重启计数 |
| `ruisheng_gw_bus_lock_timeout_total` | `bus_id` | 15s acquire 失败 |
| `ruisheng_gw_batch_drop_total` | — | queue 满 drop-tail |
| `ruisheng_gw_db_write_fail_total` | — | retry 3 次后仍失败 |
| `ruisheng_gw_wal_overflow_total` | — | WAL 10GB 丢最老 |
| `ruisheng_gw_wal_replay_fail_total` | — | 启动重放单条失败 |
| `ruisheng_gw_redis_publish_fail_total` | `channel` | pub 失败 |
| `ruisheng_gw_connections_active` | — | 当前连接数 |

## Gauges

| 名 | Labels | 说明 |
|---|---|---|
| `ruisheng_gw_build_info` | `version, sha, shared_version` | 构建信息 1 |
| `ruisheng_gw_last_frame_timestamp_seconds` | `dev_number` | 上次收到任意帧时间（Unix 秒） |
| `ruisheng_gw_batch_queue_depth` | — | 当前队列深度 |
| `ruisheng_gw_queue_high_watermark` | — | >80% full 触发 1 |
| `ruisheng_gw_backpressure_engaged` | — | flush 加速触发 1 |

## Histograms

| 名 | Labels | 说明 |
|---|---|---|
| `ruisheng_gw_flush_duration_seconds` | — | batch flush 耗时（P95 < 100ms 目标） |
| `ruisheng_gw_db_write_duration_seconds` | — | 单次 DB write 耗时 |
| `ruisheng_gw_redis_publish_duration_seconds` | `channel` | 单次 pub 耗时 |

## Scrape 配置（Plan 4 部署参考）

```yaml
scrape_configs:
  - job_name: ruisheng-gw
    scrape_interval: 15s
    static_configs:
      - targets: ['gw:9090']
```
