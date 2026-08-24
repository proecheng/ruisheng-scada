"""health server: /health /ready /metrics (aiohttp :9090)."""

from __future__ import annotations

import hashlib

import pytest
from aiohttp.test_utils import TestClient, TestServer
from ruisheng_gw import management_auth
from ruisheng_gw.health import HealthState, _health_source_acl, create_health_app

HEALTH_TOKEN = "a" * 43
HEALTH_TOKEN_DIGEST = hashlib.sha256(HEALTH_TOKEN.encode("ascii")).hexdigest()
AUTH_HEADERS = {"Authorization": f"Bearer {HEALTH_TOKEN}"}


@pytest.fixture
async def client() -> TestClient:
    state = HealthState()
    state.set_db_ok(True)
    state.set_redis_ok(True)
    state.mark_flush_ok()
    app = create_health_app(state, token_sha256=HEALTH_TOKEN_DIGEST)
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_health_returns_200(client: TestClient) -> None:
    resp = await client.get("/health", headers=AUTH_HEADERS)
    assert resp.status == 200  # noqa: PLR2004  # HTTP status literal
    data = await resp.json()
    assert data["status"] == "alive"


async def test_ready_returns_200_when_all_healthy(client: TestClient) -> None:
    resp = await client.get("/ready", headers=AUTH_HEADERS)
    assert resp.status == 200  # noqa: PLR2004  # HTTP status literal
    data = await resp.json()
    assert data["ready"] is True


async def test_ready_returns_503_when_db_down() -> None:
    state = HealthState()
    state.set_db_ok(False)
    state.set_redis_ok(True)
    state.mark_flush_ok()
    app = create_health_app(state, token_sha256=HEALTH_TOKEN_DIGEST)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/ready", headers=AUTH_HEADERS)
        assert resp.status == 503  # noqa: PLR2004  # HTTP status literal


async def test_ready_returns_503_after_batch_flush_failure() -> None:
    state = HealthState()
    state.set_db_ok(True)
    state.set_redis_ok(True)
    state.mark_flush_failed()
    app = create_health_app(state, token_sha256=HEALTH_TOKEN_DIGEST)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/ready", headers=AUTH_HEADERS)
        assert resp.status == 503  # noqa: PLR2004  # HTTP status literal

    state.mark_flush_ok()
    assert state.is_ready()


async def test_metrics_prometheus_format(client: TestClient) -> None:
    resp = await client.get("/metrics", headers=AUTH_HEADERS)
    assert resp.status == 200  # noqa: PLR2004  # HTTP status literal
    body = await resp.text()
    assert "# TYPE" in body
    assert "ruisheng_gw_alarm_outbox_relay_failures_total 0" in body


def test_readiness_fails_after_repeated_outbox_errors_and_recovers() -> None:
    state = HealthState(_db_ok=True, _redis_ok=True, _batch_ok=True)
    state.mark_outbox_relay_failed()
    state.mark_outbox_relay_failed()
    assert state.is_ready()
    state.mark_outbox_relay_failed()
    assert not state.is_ready()
    state.mark_outbox_relay_ok()
    assert state.is_ready()


async def test_health_source_acl_denies_unapproved_peer() -> None:
    app = create_health_app(
        HealthState(),
        "192.0.2.10/32",
        token_sha256=HEALTH_TOKEN_DIGEST,
    )
    async with TestClient(TestServer(app)) as denied_client:
        response = await denied_client.get(
            "/health",
            headers={
                "Authorization": f"Bearer {HEALTH_TOKEN}",
                "X-Forwarded-For": "192.0.2.10",
            },
        )
        assert response.status == 403  # noqa: PLR2004
        assert await response.json() == {"detail": "health source is not approved"}


class _MappedTransport:
    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return ("::ffff:127.0.0.1", 43123) if name == "peername" else None


async def test_health_source_acl_accepts_ipv4_mapped_ipv6_peer() -> None:
    app = create_health_app(HealthState(), "127.0.0.1/32")
    request = type(
        "RequestStub",
        (),
        {
            "transport": _MappedTransport(),
            "app": app,
            "headers": {"X-Forwarded-For": "203.0.113.1"},
        },
    )()

    sentinel = object()

    async def handler(_request: object) -> object:
        return sentinel

    response = await _health_source_acl(request, handler)
    assert response is sentinel


def test_health_source_acl_rejects_ipv4_mapped_ipv6_network() -> None:
    with pytest.raises(ValueError, match="IPv4-mapped"):
        create_health_app(HealthState(), "::ffff:127.0.0.1/128")


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
async def test_management_endpoints_reject_missing_token(path: str) -> None:
    app = create_health_app(HealthState(), token_sha256=HEALTH_TOKEN_DIGEST)
    async with TestClient(TestServer(app)) as protected_client:
        response = await protected_client.get(path)
        assert response.status == 403  # noqa: PLR2004
        assert await response.json() == {"detail": "management access denied"}


async def test_management_endpoint_rejects_wrong_token_without_echo() -> None:
    app = create_health_app(HealthState(), token_sha256=HEALTH_TOKEN_DIGEST)
    wrong_token = "b" * 43
    async with TestClient(TestServer(app)) as protected_client:
        response = await protected_client.get(
            "/health",
            headers={"Authorization": f"Bearer {wrong_token}"},
        )
        assert response.status == 403  # noqa: PLR2004
        assert wrong_token not in await response.text()


async def test_management_endpoint_accepts_correct_token() -> None:
    app = create_health_app(HealthState(), token_sha256=HEALTH_TOKEN_DIGEST)
    async with TestClient(TestServer(app)) as protected_client:
        response = await protected_client.get(
            "/health",
            headers={"Authorization": f"Bearer {HEALTH_TOKEN}"},
        )
        assert response.status == 200  # noqa: PLR2004
        assert HEALTH_TOKEN not in await response.text()


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Basic " + HEALTH_TOKEN,
        "Bearer " + "a" * 42,
        "Bearer " + "a" * 257,
        "Bearer " + "a" * 21 + " " + "a" * 22,
        "Bearer " + "a" * 42 + "é",
        "Bearer " + "a" * 42 + "!",
    ],
)
def test_management_token_rejects_malformed_values(authorization: str | None) -> None:
    assert not management_auth.management_bearer_matches(authorization, HEALTH_TOKEN_DIGEST)


def test_management_token_uses_constant_time_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(management_auth.secrets, "compare_digest", _compare)
    assert management_auth.management_bearer_matches(f"Bearer {HEALTH_TOKEN}", HEALTH_TOKEN_DIGEST)
    assert calls == [(HEALTH_TOKEN_DIGEST, HEALTH_TOKEN_DIGEST)]


def test_management_token_missing_digest_fails_closed() -> None:
    assert not management_auth.management_bearer_matches(f"Bearer {HEALTH_TOKEN}", None)
