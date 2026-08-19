"""微信模板消息适配器。token 来自 wx_groups 表（spec §4.2）。"""

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


class WechatNotifier:
    name = "wechat"

    def __init__(self, *, access_token: str, template_id: str) -> None:
        self._token = access_token
        self._template = template_id

    async def send(self, n: AlarmNotification) -> bool:
        return (await self.send_outcome(n)).sent

    async def send_outcome(self, n: AlarmNotification) -> ProviderResult:
        url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={self._token}"
        payload = {
            "touser": n.contact,
            "template_id": self._template,
            "data": {
                "first": {"value": f"设备 {n.dev_number} 告警"},
                "keyword1": {"value": n.alarm_name},
                "keyword2": {"value": f"{n.value} (阈值 {n.limit})"},
                "remark": {"value": n.msg},
            },
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s,
                s.post(url, json=payload) as resp,
            ):
                data = await resp.json()
                if data.get("errcode") == 0:
                    return ProviderResult(sent=True)
                errcode = data.get("errcode")
                if errcode in {-1, 40001, 40014, 42001, 45009, 45011, 45047}:
                    rate_limited = errcode in {45009, 45011, 45047}
                    return ProviderResult(
                        sent=False,
                        retryable=True,
                        error_class=(
                            "rate_limited"
                            if rate_limited
                            else "token_refresh_required"
                            if errcode in {40001, 40014, 42001}
                            else "server_error"
                        ),
                        http_status=resp.status,
                        retry_after_sec=parse_retry_after(
                            resp.headers.get("Retry-After"),
                            reference_time=parse_http_date(resp.headers.get("Date")),
                        ),
                    )
                status = resp.status if isinstance(resp.status, int) else 400
                if status == HTTP_RATE_LIMITED or status >= HTTP_SERVER_ERROR:
                    return ProviderResult(
                        sent=False,
                        retryable=True,
                        error_class=(
                            "rate_limited" if status == HTTP_RATE_LIMITED else "server_error"
                        ),
                        http_status=status,
                        retry_after_sec=parse_retry_after(
                            resp.headers.get("Retry-After"),
                            reference_time=parse_http_date(resp.headers.get("Date")),
                        ),
                    )
                logger.bind(errcode=data.get("errcode")).warning("wechat send failed")
                return ProviderResult(
                    sent=False,
                    error_class="invalid_target",
                    http_status=status,
                )
        except aiohttp.ClientError:
            logger.exception("wechat send client error")
            return ProviderResult(sent=False, retryable=True, error_class="transport")
