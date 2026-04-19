"""WebSocket 端点：/ws。Query ?token=<access_jwt>。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from ..core.security import client_fingerprint, verify_token
from ..pubsub.ws_manager import WSClient, WSManager

router = APIRouter(tags=["ws"])


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
    try:
        while True:
            raw = await ws.receive_text()
            try:
                obj = json.loads(raw)
                if obj.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except (ValueError, TypeError):
                continue
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        await manager.remove(client)
        logger.bind(user_name=client.user_name).info("ws disconnected")
