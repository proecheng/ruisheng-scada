import hashlib

import pytest
from pydantic import ValidationError
from ruisheng_api.config import Config

MANAGEMENT_TOKEN = "a" * 43
MANAGEMENT_TOKEN_DIGEST = hashlib.sha256(MANAGEMENT_TOKEN.encode("ascii")).hexdigest()


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


def test_prod_requires_management_token_digest(monkeypatch):
    _env(monkeypatch, API_ENV="prod")
    with pytest.raises(ValidationError, match="API_MANAGEMENT_TOKEN_SHA256"):
        Config()


@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "A" * 64, "g" * 64, f" {MANAGEMENT_TOKEN_DIGEST}"],
)
def test_management_token_digest_must_be_lowercase_sha256(monkeypatch, digest):
    _env(monkeypatch, API_MANAGEMENT_TOKEN_SHA256=digest)
    with pytest.raises(ValidationError, match="64 lowercase hex"):
        Config()


def test_prod_accepts_valid_management_token_digest(monkeypatch):
    _env(
        monkeypatch,
        API_ENV="prod",
        API_MANAGEMENT_TOKEN_SHA256=MANAGEMENT_TOKEN_DIGEST,
    )
    assert Config().management_token_sha256 == MANAGEMENT_TOKEN_DIGEST


def test_invalid_management_digest_does_not_echo_input(monkeypatch):
    plaintext = "SYNTHETIC-PLAINTEXT-MANAGEMENT-TOKEN-" + "x" * 32
    _env(monkeypatch, API_MANAGEMENT_TOKEN_SHA256=plaintext)
    with pytest.raises(ValidationError) as captured:
        Config()
    assert plaintext not in str(captured.value)
