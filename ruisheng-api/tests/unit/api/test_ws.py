import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.main import create_app
from ruisheng_api.pubsub.ws_manager import WSManager


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
