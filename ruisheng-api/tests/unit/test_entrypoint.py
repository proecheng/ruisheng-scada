"""API process entrypoint logging safety."""

from __future__ import annotations

import logging

from ruisheng_api.__main__ import RedactRequestQueryFilter, _safe_log_config


def test_uvicorn_websocket_query_token_is_redacted() -> None:
    secret = "B04_DEDICATED_TOKEN_4f6cb9"
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:1234", f"/ws?token={secret}"),
        None,
    )

    assert RedactRequestQueryFilter().filter(record)
    message = record.getMessage()
    assert secret not in message
    assert "token=" not in message
    assert "WebSocket /ws" in message


def test_all_uvicorn_handlers_install_query_redaction() -> None:
    config = _safe_log_config()

    assert set(config["handlers"]) == {"default", "access"}
    assert all(
        handler["filters"] == ["redact_request_query"] for handler in config["handlers"].values()
    )
