from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ruisheng_api.services.notification.base import AlarmNotification
from ruisheng_api.services.notification.wechat import WechatNotifier


def _notif():
    return AlarmNotification(
        trace_id="t1",
        event_id=1,
        dev_number="d1",
        alarm_name="test",
        value=1.0,
        limit=0.5,
        user_name="u",
        contact="openid_123",
        msg="msg",
    )


@pytest.mark.asyncio
async def test_wechat_send_success():
    notifier = WechatNotifier(access_token="tok", template_id="tpl")
    mock_resp = MagicMock()
    mock_resp.json = AsyncMock(return_value={"errcode": 0})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession") as mock_session:
        mock_session_inst = MagicMock()
        mock_session_inst.post = MagicMock(return_value=mock_resp)
        mock_session_inst.__aenter__ = AsyncMock(return_value=mock_session_inst)
        mock_session_inst.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_session_inst
        result = await notifier.send(_notif())

    assert result is True


@pytest.mark.asyncio
async def test_wechat_send_failure_errcode():
    notifier = WechatNotifier(access_token="tok", template_id="tpl")
    mock_resp = MagicMock()
    mock_resp.json = AsyncMock(return_value={"errcode": 40003})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession") as mock_session:
        mock_session_inst = MagicMock()
        mock_session_inst.post = MagicMock(return_value=mock_resp)
        mock_session_inst.__aenter__ = AsyncMock(return_value=mock_session_inst)
        mock_session_inst.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_session_inst
        result = await notifier.send(_notif())

    assert result is False
