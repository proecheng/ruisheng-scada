import time

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.main import create_app
from ruisheng_api.pubsub.ws_manager import WSManager
from starlette.websockets import WebSocketDisconnect


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def test_ws_requires_valid_token(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    # Set state before TestClient so the WS endpoint has what it needs
    app.state.ws_manager = WSManager()
    app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    client = TestClient(app)
    try:
        with client.websocket_connect("/ws?token=invalid"):
            raise AssertionError("should have refused")
    except Exception:
        pass  # Rejected connection — test passes


def test_ws_accepts_valid_token(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    # Set state before TestClient so the WS endpoint has what it needs
    app.state.ws_manager = WSManager()
    app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    fp = client_fingerprint("testclient", "testclient")
    tok = issue_access_token("alice", "g1", "User", 0, fp, secret="x" * 64, ttl_sec=900)
    client = TestClient(app)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        ws.send_text('{"type":"ping"}')
        msg = ws.receive_text()
        assert "pong" in msg


def test_ws_closes_at_the_session_deadline_without_client_traffic(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    app.state.ws_manager = WSManager()
    app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    fp = client_fingerprint("testclient", "testclient")
    token = issue_access_token(
        "alice",
        "g1",
        "User",
        0,
        fp,
        secret="x" * 64,
        ttl_sec=900,
        session_expires_at=int(time.time()) + 2,
    )
    with TestClient(app).websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_ws_rejects_revoked_token(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    app.state.ws_manager = WSManager()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.redis = redis
    fp = client_fingerprint("testclient", "testclient")
    tok = issue_access_token("alice", "g1", "User", 0, fp, secret="x" * 64, ttl_sec=900)
    import asyncio

    from ruisheng_api.core.security import verify_token
    from ruisheng_api.core.token_blacklist import blacklist_jti

    jti = str(verify_token(tok, secret="x" * 64, expected_fp=fp)["jti"])
    asyncio.run(blacklist_jti(redis, jti, 60))
    with pytest.raises(WebSocketDisconnect), TestClient(app).websocket_connect(f"/ws?token={tok}"):
        pass
