"""pydantic-settings config 验证。"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError
from ruisheng_gw.config import Config


def test_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    cfg = Config()
    assert cfg.listen_host == "0.0.0.0"
    assert cfg.listen_port == 5020  # noqa: PLR2004  # test fixture literal


def test_config_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # 清空所有 GW_* env，需要项缺失时 pydantic raise
    for key in list(_iter_env_vars()):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Config()


def test_config_extra_forbid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("GW_UNKNOWN_FIELD", "oops")
    # extra="forbid" → raise
    with pytest.raises(ValidationError, match="extra"):
        Config()


def _iter_env_vars() -> list[str]:
    return [k for k in os.environ if k.startswith("GW_")]


def test_serial_ports_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "6000")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    cfg = Config()
    assert cfg.serial_ports == []


def test_serial_ports_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "6000")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv(
        "GW_SERIAL_PORTS",
        json.dumps([{"port": "COM3", "baud_rate": 9600}]),
    )
    cfg = Config()
    assert len(cfg.serial_ports) == 1
    assert cfg.serial_ports[0].port == "COM3"
    assert cfg.serial_ports[0].baud_rate == 9600
