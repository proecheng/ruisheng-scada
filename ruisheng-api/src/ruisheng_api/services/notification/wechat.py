"""微信模板消息适配器。token 来自 wx_groups 表（spec §4.2）。"""

from __future__ import annotations

import aiohttp
from loguru import logger

from .base import AlarmNotification


class WechatNotifier:
    name = "wechat"

    def __init__(self, *, access_token: str, template_id: str) -> None:
        self._token = access_token
        self._template = template_id

    async def send(self, n: AlarmNotification) -> bool:
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
                    return True
                logger.bind(errcode=data.get("errcode")).warning("wechat send failed")
                return False
        except aiohttp.ClientError:
            logger.exception("wechat send client error")
            return False
