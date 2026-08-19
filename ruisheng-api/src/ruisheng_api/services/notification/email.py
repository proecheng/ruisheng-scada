"""SMTP 邮件适配器。"""

from __future__ import annotations

import asyncio
import contextlib
import smtplib
from email.message import EmailMessage

from loguru import logger

from .base import HTTP_SERVER_ERROR, AlarmNotification, ProviderResult

SMTP_TRANSIENT_ERROR = 400


class EmailNotifier:
    name = "email"

    def __init__(self, *, host: str, port: int, user: str, password: str, tls: bool = True) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._pw = password
        self._tls = tls

    def _send_sync(self, n: AlarmNotification) -> ProviderResult:
        msg = EmailMessage()
        msg["From"] = self._user
        msg["To"] = n.contact
        msg["Subject"] = f"[ruisheng] 告警 {n.dev_number}"
        msg.set_content(f"{n.alarm_name}: {n.value} (limit {n.limit})\n\n{n.msg}")
        server: smtplib.SMTP | None = None
        try:
            server = smtplib.SMTP(self._host, self._port, timeout=10)
            if self._tls:
                server.starttls()
            server.login(self._user, self._pw)
            server.send_message(msg)
            return ProviderResult(sent=True)
        except smtplib.SMTPAuthenticationError:
            logger.warning("smtp authentication failed")
            return ProviderResult(sent=False, error_class="authentication")
        except smtplib.SMTPRecipientsRefused:
            logger.warning("smtp recipient rejected")
            return ProviderResult(sent=False, error_class="invalid_target")
        except smtplib.SMTPResponseException as exc:
            retryable = SMTP_TRANSIENT_ERROR <= exc.smtp_code < HTTP_SERVER_ERROR
            logger.bind(status=exc.smtp_code).warning("smtp request rejected")
            return ProviderResult(
                sent=False,
                retryable=retryable,
                error_class="server_error" if retryable else "rejected",
            )
        except (TimeoutError, OSError, smtplib.SMTPServerDisconnected):
            logger.exception("smtp send failed")
            return ProviderResult(sent=False, retryable=True, error_class="transport")
        except smtplib.SMTPException:
            logger.exception("smtp protocol failed")
            return ProviderResult(sent=False, retryable=True, error_class="protocol")
        finally:
            if server is not None:
                try:
                    server.quit()
                except (OSError, smtplib.SMTPException):
                    with contextlib.suppress(OSError, smtplib.SMTPException):
                        server.close()

    async def send(self, n: AlarmNotification) -> bool:
        return (await self.send_outcome(n)).sent

    async def send_outcome(self, n: AlarmNotification) -> ProviderResult:
        return await asyncio.to_thread(self._send_sync, n)
