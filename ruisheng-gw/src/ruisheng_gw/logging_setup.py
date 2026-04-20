"""structlog JSON 输出 + contextvars-based correlation ID 自动注入。

log record 必带字段：
  timestamp, level, event, module
  + context: conn_id, dev_number, bus_id, usr_group, tenant_id（按需 bind）
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TextIO

import structlog
from structlog.contextvars import bind_contextvars, unbind_contextvars


def configure_logging(
    *,
    level: str = "INFO",
    stream: TextIO | None = None,
) -> None:
    stream = stream or sys.stdout
    handler = logging.StreamHandler(stream)
    logging.basicConfig(
        level=level.upper(),
        handlers=[handler],
        format="%(message)s",
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


@contextmanager
def bind_context(**kwargs: Any) -> Iterator[None]:
    bind_contextvars(**kwargs)
    try:
        yield
    finally:
        unbind_contextvars(*kwargs)
