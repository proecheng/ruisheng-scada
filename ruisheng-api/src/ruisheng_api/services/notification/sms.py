"""SMS 通知适配器。当前实现 custom_http（POST JSON）骨架。"""

from __future__ import annotations

import aiohttp
from loguru import logger

from .base import AlarmNotification

_HTTP_ERROR_THRESHOLD = 400


class CustomHttpSmsNotifier:
    """POST {phone_number, content} to configurable endpoint。"""

    name = "sms_custom_http"

    def __init__(self, *, endpoint: str, api_key: str = "") -> None:
        self._endpoint = endpoint
        self._api_key = api_key

    async def send(self, n: AlarmNotification) -> bool:
        payload = {
            "phone_number": n.contact,
            "content": f"[{n.alarm_name}] 设备 {n.dev_number}: {n.value} (阈值 {n.limit}) — {n.msg}",
        }
        headers = {"X-Api-Key": self._api_key} if self._api_key else {}
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s,
                s.post(self._endpoint, json=payload, headers=headers) as resp,
            ):
                if resp.status < _HTTP_ERROR_THRESHOLD:
                    return True
                logger.bind(status=resp.status).warning("sms send failed")
                return False
        except aiohttp.ClientError:
            logger.exception("sms http error")
            return False
