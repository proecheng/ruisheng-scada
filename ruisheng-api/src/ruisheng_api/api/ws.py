"""WebSocket 端点：/ws。Query ?token=<access_jwt>。"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from ..core.security import client_fingerprint, session_deadline, verify_token
from ..core.token_blacklist import is_jti_blacklisted
from ..pubsub.ws_manager import WSClient, WSManager

router = APIRouter(tags=["ws"])


async def _close_if_revoked(ws: WebSocket, jti: str) -> bool:
    try:
        revoked = await is_jti_blacklisted(ws.app.state.redis, jti)
    except Exception:
        return False
    if revoked:
        await ws.close(code=1008)
    return revoked


async def _watch_revocation(ws: WebSocket, jti: str) -> None:
    while True:
        await asyncio.sleep(5)
        if await _close_if_revoked(ws, jti):
            return


async def _pump(ws: WebSocket, client: WSClient) -> None:
    """从 client.queue 取消息发 WS。"""
    try:
        while True:
            msg = await client.queue.get()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        pass


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str = Query(...),
) -> None:
    cfg = ws.app.state.config
    manager: WSManager = ws.app.state.ws_manager
    ip = ws.client.host if ws.client else "unknown"
    ua = ws.headers.get("user-agent", "")
    fp = client_fingerprint(ip, ua)
    try:
        payload = verify_token(token, secret=cfg.jwt_secret, expected_fp=fp)
        deadline = min(int(str(payload["exp"])), session_deadline(payload, cfg.jwt_session_ttl_sec))
        jti = str(payload.get("jti") or "")
        if await is_jti_blacklisted(ws.app.state.redis, jti):
            raise RuntimeError("token revoked")
    except Exception:
        await ws.close(code=1008)
        return
    await ws.accept()
    client = WSClient(
        ws=ws,
        user_name=str(payload["sub"]),
        usr_group=str(payload.get("usr_group") or ""),
        role=str(payload["role"]),
    )
    await manager.add(client)
    logger.bind(user_name=client.user_name).info("ws connected")
    pump = asyncio.create_task(_pump(ws, client))
    watch = asyncio.create_task(_watch_revocation(ws, jti))
    try:
        async with asyncio.timeout(max(0, deadline - time.time())):
            while True:
                raw = await ws.receive_text()
                if await _close_if_revoked(ws, jti):
                    return
                try:
                    obj = json.loads(raw)
                    if obj.get("type") == "ping":
                        await ws.send_text(json.dumps({"type": "pong"}))
                except (ValueError, TypeError):
                    continue
    except TimeoutError:
        await ws.close(code=1008)
    except WebSocketDisconnect:
        pass
    finally:
        watch.cancel()
        pump.cancel()
        await asyncio.gather(watch, pump, return_exceptions=True)
        await manager.remove(client)
        logger.bind(user_name=client.user_name).info("ws disconnected")
