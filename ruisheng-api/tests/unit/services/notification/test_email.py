from unittest.mock import MagicMock

import pytest
from ruisheng_api.services.notification.base import AlarmNotification
from ruisheng_api.services.notification.email import EmailNotifier


def _notif():
    return AlarmNotification(
        trace_id="t1",
        event_id=1,
        dev_number="d1",
        alarm_name="test",
        value=1.0,
        limit=0.5,
        user_name="u",
        contact="test@example.com",
        msg="msg",
    )


@pytest.mark.asyncio
async def test_email_send_success(monkeypatch):
    notifier = EmailNotifier(host="smtp.test", port=587, user="u@test.com", password="pw")
    mock_smtp = MagicMock()
    mock_smtp.return_value = mock_smtp
    monkeypatch.setattr("smtplib.SMTP", mock_smtp)
    result = await notifier.send(_notif())
    assert result is True
    mock_smtp.return_value.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_email_send_failure(monkeypatch):
    notifier = EmailNotifier(host="smtp.test", port=587, user="u@test.com", password="pw")

    def fail_smtp(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP", fail_smtp)
    result = await notifier.send(_notif())
    assert result is False
