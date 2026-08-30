"""`python -m ruisheng_api` 启 uvicorn。"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

import uvicorn

from .config import Config

QUERY_TARGET_RE = re.compile(r"(?P<path>/[^?\s\"]*)\?[^\s\"]+")


class RedactRequestQueryFilter(logging.Filter):
    """Remove query strings before Uvicorn formats HTTP/WebSocket log records."""

    @staticmethod
    def _redact(value: Any) -> Any:
        return QUERY_TARGET_RE.sub(r"\g<path>", value) if isinstance(value, str) else value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact(value) for key, value in record.args.items()}
        return True


def _safe_log_config() -> dict[str, Any]:
    config: dict[str, Any] = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["filters"] = {"redact_request_query": {"()": RedactRequestQueryFilter}}
    for handler in config["handlers"].values():
        handler["filters"] = ["redact_request_query"]
    return config


def main() -> None:
    cfg = Config()
    uvicorn.run(
        "ruisheng_api.main:create_app",
        factory=True,
        host=cfg.listen_host,
        port=cfg.listen_port,
        log_level="info",
        log_config=_safe_log_config(),
    )


if __name__ == "__main__":
    main()
