import pytest
from pydantic import ValidationError
from ruisheng_api.config import Config


def _env(monkeypatch, **overrides):
    base = {
        "API_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_GW_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_REDIS_URL": "redis://:p@h/0",
        "API_JWT_SECRET": "x" * 64,
    }
    for k in list(base):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**base, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_config_happy(monkeypatch):
    _env(monkeypatch)
    c = Config()
    assert c.db_url.startswith("postgresql+asyncpg")
    assert c.listen_port == 8000
    assert c.jwt_access_ttl_sec == 900


def test_config_missing_required(monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("API_JWT_SECRET")
    with pytest.raises(ValidationError):
        Config()


def test_config_extra_forbid(monkeypatch):
    _env(monkeypatch, API_UNKNOWN="x")
    with pytest.raises(ValueError, match="extra unknown API_"):
        Config()


def test_config_jwt_secret_too_short(monkeypatch):
    _env(monkeypatch, API_JWT_SECRET="short")
    with pytest.raises(ValidationError):
        Config()
