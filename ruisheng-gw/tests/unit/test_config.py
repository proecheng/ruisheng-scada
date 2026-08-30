"""pydantic-settings config 验证。"""

from __future__ import annotations

import hashlib
import os

import pytest
from pydantic import ValidationError
from ruisheng_gw.config import Config

HEALTH_TOKEN = "a" * 43
HEALTH_TOKEN_DIGEST = hashlib.sha256(HEALTH_TOKEN.encode("ascii")).hexdigest()


@pytest.fixture(autouse=True)
def _test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_ENV", "test")


def test_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    cfg = Config()
    assert cfg.listen_host == "0.0.0.0"
    assert cfg.listen_port == 5020  # noqa: PLR2004  # test fixture literal
    assert cfg.health_host == "127.0.0.1"


def test_health_host_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("GW_HEALTH_HOST", "10.20.30.40")
    cfg = Config()
    assert cfg.health_host == "10.20.30.40"


@pytest.mark.parametrize("value", ["", "localhost", "health.internal"])
def test_health_host_rejects_non_ip_literal(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("GW_HEALTH_HOST", value)
    with pytest.raises(ValidationError, match="health_host must be an IPv4 or IPv6 address"):
        Config()


@pytest.mark.parametrize("value", ["0.0.0.0", "::", "::1"])
def test_health_host_accepts_ip_listener_addresses(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("GW_HEALTH_HOST", value)
    assert Config().health_host == value


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


def test_prod_requires_health_token_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_ENV", "prod")
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.delenv("GW_HEALTH_TOKEN_SHA256", raising=False)
    with pytest.raises(ValidationError, match="GW_HEALTH_TOKEN_SHA256"):
        Config()


@pytest.mark.parametrize("digest", ["a" * 63, "A" * 64, "g" * 64, f" {HEALTH_TOKEN_DIGEST}"])
def test_health_token_digest_must_be_lowercase_sha256(
    monkeypatch: pytest.MonkeyPatch, digest: str
) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("GW_HEALTH_TOKEN_SHA256", digest)
    with pytest.raises(ValidationError, match="64 lowercase hex"):
        Config()


def test_prod_accepts_valid_health_token_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_ENV", "prod")
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("GW_HEALTH_TOKEN_SHA256", HEALTH_TOKEN_DIGEST)
    assert Config().health_token_sha256 == HEALTH_TOKEN_DIGEST


def test_invalid_health_digest_does_not_echo_input(monkeypatch: pytest.MonkeyPatch) -> None:
    plaintext = "SYNTHETIC-PLAINTEXT-MANAGEMENT-TOKEN-" + "x" * 32
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("GW_HEALTH_TOKEN_SHA256", plaintext)
    with pytest.raises(ValidationError) as captured:
        Config()
    assert plaintext not in str(captured.value)
