import hashlib

import pytest
from fastapi.testclient import TestClient
from ruisheng_api.main import create_app

MANAGEMENT_TOKEN = "a" * 43
MANAGEMENT_TOKEN_DIGEST = hashlib.sha256(MANAGEMENT_TOKEN.encode("ascii")).hexdigest()


def _set_required_env(monkeypatch, **overrides):
    values = {
        "API_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_GW_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_REDIS_URL": "redis://:p@h/0",
        "API_JWT_SECRET": "x" * 64,
        **overrides,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_app_health_liveness(monkeypatch):
    _set_required_env(monkeypatch, API_MANAGEMENT_TOKEN_SHA256=MANAGEMENT_TOKEN_DIGEST)

    app = create_app()
    r = TestClient(app).get(
        "/api/health/live",
        headers={"Authorization": f"Bearer {MANAGEMENT_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert r.json()["data"]["status"] == "live"


def test_app_health_fails_closed_without_digest(monkeypatch):
    _set_required_env(monkeypatch)
    response = TestClient(create_app()).get("/api/health/live")
    assert response.status_code == 403


def test_app_openapi_title(monkeypatch):
    _set_required_env(monkeypatch)

    app = create_app()
    spec = app.openapi()
    assert spec["info"]["title"] == "ruisheng-api"


@pytest.mark.parametrize(
    "path",
    ["/api/health/live", "/api/health/ready", "/api/health/metrics"],
)
def test_prod_management_endpoints_reject_missing_token(monkeypatch, path):
    _set_required_env(
        monkeypatch,
        API_ENV="prod",
        API_MANAGEMENT_TOKEN_SHA256=MANAGEMENT_TOKEN_DIGEST,
    )
    response = TestClient(create_app()).get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "management access denied"}


def test_prod_management_endpoint_rejects_wrong_token_without_echo(monkeypatch):
    _set_required_env(
        monkeypatch,
        API_ENV="prod",
        API_MANAGEMENT_TOKEN_SHA256=MANAGEMENT_TOKEN_DIGEST,
    )
    wrong_token = "b" * 43
    response = TestClient(create_app()).get(
        "/api/health/live",
        headers={"Authorization": f"Bearer {wrong_token}"},
    )
    assert response.status_code == 403
    assert wrong_token not in response.text


def test_prod_management_endpoint_accepts_correct_token(monkeypatch):
    _set_required_env(
        monkeypatch,
        API_ENV="prod",
        API_MANAGEMENT_TOKEN_SHA256=MANAGEMENT_TOKEN_DIGEST,
    )
    response = TestClient(create_app()).get(
        "/api/health/live",
        headers={"Authorization": f"Bearer {MANAGEMENT_TOKEN}"},
    )
    assert response.status_code == 200
    assert MANAGEMENT_TOKEN not in response.text
