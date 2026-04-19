"""定时（每 50 min）刷新所有 wx_groups 的 access_token。"""

from __future__ import annotations

import aiohttp
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def refresh_all_wechat_tokens(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        rows = await session.execute(
            text(
                "SELECT usr_group, appid, appsecret FROM wx_groups "
                "WHERE appid IS NOT NULL AND appsecret IS NOT NULL"
            )
        )
        groups = list(rows)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
        for g in groups:
            url = (
                "https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential"
                f"&appid={g.appid}&secret={g.appsecret}"
            )
            try:
                async with http.get(url) as resp:
                    data = await resp.json()
                    token = data.get("access_token")
                    if not token:
                        logger.bind(usr_group=g.usr_group).warning(
                            "wx token refresh: no access_token"
                        )
                        continue
                    async with factory() as s, s.begin():
                        await s.execute(
                            text("SELECT set_config('app.role', 'Administrators', true)")
                        )
                        await s.execute(
                            text(
                                "UPDATE wx_groups SET token = :t, "
                                "token_expires_at = now() + interval '7200 seconds' "
                                "WHERE usr_group = :g"
                            ),
                            {"t": token, "g": g.usr_group},
                        )
            except aiohttp.ClientError:
                logger.exception("wx token refresh http error")
