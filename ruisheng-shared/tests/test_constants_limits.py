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
    assert JWT_ACCESS_TTL_S == 900  # 15 min
    assert JWT_REFRESH_TTL_S == 604800  # 7 d


def test_stream_maxlen() -> None:
    assert STREAM_ALARM_MAXLEN == 100000
    assert STREAM_CONTROL_MAXLEN == 50000


def test_ws_queue() -> None:
    assert WS_SEND_QUEUE_MAX == 500


def test_log_disk() -> None:
    assert LOG_DISK_CAP_GB == 20
    assert LOG_ROTATE_SIZE_MB == 100
