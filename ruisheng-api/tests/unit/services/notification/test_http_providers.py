from __future__ import annotations

from ruisheng_api.services.notification.base import AlarmNotification
from ruisheng_api.services.notification.sms import CustomHttpSmsNotifier
from ruisheng_api.services.notification.voice import CustomHttpVoiceNotifier
from ruisheng_api.services.notification.wechat import WechatNotifier


def _notification() -> AlarmNotification:
    return AlarmNotification(
        trace_id="trace-1",
        event_id=1,
        dev_number="D1",
        alarm_name="high",
        value=10.0,
        limit=5.0,
        user_name="alice",
        contact="target",
        msg="alarm",
    )


class _Response:
    def __init__(self, status: int, data: dict[str, object] | None = None) -> None:
        self.status = status
        self.headers: dict[str, str] = {}
        self._data = data or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self) -> dict[str, object]:
        return self._data


class _Session:
    response = _Response(200)

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def post(self, *args, **kwargs) -> _Response:
        return self.response


async def test_wechat_http_200_rate_limit_is_retryable(monkeypatch) -> None:
    _Session.response = _Response(200, {"errcode": 45009})
    monkeypatch.setattr("ruisheng_api.services.notification.wechat.aiohttp.ClientSession", _Session)
    notifier = WechatNotifier(access_token="token", template_id="template")
    result = await notifier.send_outcome(_notification())
    assert result.retryable
    assert result.error_class == "rate_limited"


async def test_wechat_expired_token_codes_are_retryable(monkeypatch) -> None:
    _Session.response = _Response(200, {"errcode": 42001})
    monkeypatch.setattr("ruisheng_api.services.notification.wechat.aiohttp.ClientSession", _Session)
    result = await WechatNotifier(
        access_token="expired-token", template_id="template"
    ).send_outcome(_notification())
    assert result.retryable
    assert result.error_class == "token_refresh_required"


async def test_sms_redirect_is_not_recorded_as_sent(monkeypatch) -> None:
    _Session.response = _Response(302)
    monkeypatch.setattr("ruisheng_api.services.notification.sms.aiohttp.ClientSession", _Session)
    result = await CustomHttpSmsNotifier(endpoint="https://example.test").send_outcome(
        _notification()
    )
    assert not result.sent
    assert not result.retryable


async def test_voice_informational_response_is_not_recorded_as_sent(monkeypatch) -> None:
    _Session.response = _Response(101)
    monkeypatch.setattr("ruisheng_api.services.notification.voice.aiohttp.ClientSession", _Session)
    result = await CustomHttpVoiceNotifier(endpoint="https://example.test").send_outcome(
        _notification()
    )
    assert not result.sent
    assert not result.retryable
