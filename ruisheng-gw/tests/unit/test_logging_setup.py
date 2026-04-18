"""structlog JSON 输出 + context vars 自动注入。"""

from __future__ import annotations

import json
from io import StringIO

from ruisheng_gw.logging_setup import bind_context, configure_logging, get_logger


def test_json_line_output_has_required_fields() -> None:
    buf = StringIO()
    configure_logging(stream=buf, level="INFO")
    log = get_logger(__name__)
    log.info("hello", foo=42)
    line = buf.getvalue().strip()
    data = json.loads(line)
    assert data["event"] == "hello"
    assert data["foo"] == 42  # noqa: PLR2004  # test fixture literal
    assert data["level"] == "info"
    assert "timestamp" in data


def test_context_vars_auto_injected() -> None:
    buf = StringIO()
    configure_logging(stream=buf, level="INFO")
    log = get_logger(__name__)
    with bind_context(conn_id="c1", dev_number="DEV-001", bus_id="1.2.3.4:5020"):
        log.info("poll_done")
    data = json.loads(buf.getvalue().strip())
    assert data["conn_id"] == "c1"
    assert data["dev_number"] == "DEV-001"
    assert data["bus_id"] == "1.2.3.4:5020"
