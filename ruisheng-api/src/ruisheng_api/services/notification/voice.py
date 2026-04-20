"""語音通知適配器（骨架，參考 SMS custom_http 模式）。"""

from __future__ import annotations

import aiohttp
from loguru import logger

from .base import AlarmNotification

_HTTP_ERROR_THRESHOLD = 400


class CustomHttpVoiceNotifier:
    name = "voice_custom_http"

    def __init__(self, *, endpoint: str, api_key: str = "") -> None:
        self._endpoint = endpoint
        self._api_key = api_key

    async def send(self, n: AlarmNotification) -> bool:
        payload = {
            "phone_number": n.contact,
            "content": f"设备 {n.dev_number} 告警: {n.alarm_name}，当前值 {n.value}，请处理。",
        }
        headers = {"X-Api-Key": self._api_key} if self._api_key else {}
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s,
                s.post(self._endpoint, json=payload, headers=headers) as resp,
            ):
                if resp.status < _HTTP_ERROR_THRESHOLD:
                    return True
                logger.bind(status=resp.status).warning("voice send failed")
                return False
        except aiohttp.ClientError:
            logger.exception("voice http error")
            return False
