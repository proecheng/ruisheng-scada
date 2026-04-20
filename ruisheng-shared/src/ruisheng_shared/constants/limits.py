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
