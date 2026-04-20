"""SMTP 邮件适配器。"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from loguru import logger

from .base import AlarmNotification


class EmailNotifier:
    name = "email"

    def __init__(self, *, host: str, port: int, user: str, password: str, tls: bool = True) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._pw = password
        self._tls = tls

    def _send_sync(self, n: AlarmNotification) -> bool:
        msg = EmailMessage()
        msg["From"] = self._user
        msg["To"] = n.contact
        msg["Subject"] = f"[ruisheng] 告警 {n.dev_number}"
        msg.set_content(f"{n.alarm_name}: {n.value} (limit {n.limit})\n\n{n.msg}")
        try:
            server = smtplib.SMTP(self._host, self._port, timeout=10)
            if self._tls:
                server.starttls()
            server.login(self._user, self._pw)
            server.send_message(msg)
            server.quit()
            return True
        except Exception:
            logger.exception("smtp send failed")
            return False

    async def send(self, n: AlarmNotification) -> bool:
        return await asyncio.to_thread(self._send_sync, n)
