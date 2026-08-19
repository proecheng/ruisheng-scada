"""語音通知適配器（骨架，參考 SMS custom_http 模式）。"""

from __future__ import annotations

import aiohttp
from loguru import logger

from .base import (
    HTTP_RATE_LIMITED,
    HTTP_SERVER_ERROR,
    AlarmNotification,
    ProviderResult,
    parse_http_date,
    parse_retry_after,
)

_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300


class CustomHttpVoiceNotifier:
    name = "voice_custom_http"

    def __init__(self, *, endpoint: str, api_key: str = "") -> None:
        self._endpoint = endpoint
        self._api_key = api_key

    async def send(self, n: AlarmNotification) -> bool:
        return (await self.send_outcome(n)).sent

    async def send_outcome(self, n: AlarmNotification) -> ProviderResult:
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
                if _HTTP_SUCCESS_MIN <= resp.status < _HTTP_SUCCESS_MAX:
                    return ProviderResult(sent=True)
                retryable = resp.status == HTTP_RATE_LIMITED or resp.status >= HTTP_SERVER_ERROR
                logger.bind(status=resp.status).warning("voice send failed")
                return ProviderResult(
                    sent=False,
                    retryable=retryable,
                    error_class=(
                        "rate_limited"
                        if resp.status == HTTP_RATE_LIMITED
                        else "server_error"
                        if resp.status >= HTTP_SERVER_ERROR
                        else "authentication"
                        if resp.status in {401, 403}
                        else "invalid_target"
                    ),
                    http_status=resp.status,
                    retry_after_sec=parse_retry_after(
                        resp.headers.get("Retry-After"),
                        reference_time=parse_http_date(resp.headers.get("Date")),
                    ),
                )
        except aiohttp.ClientError:
            logger.exception("voice http error")
            return ProviderResult(sent=False, retryable=True, error_class="transport")
