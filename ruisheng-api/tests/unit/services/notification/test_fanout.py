import pytest
from ruisheng_api.services.notification.base import AlarmNotification
from ruisheng_api.services.notification.fanout import fan_out


def _notif():
    return AlarmNotification(
        trace_id="t1",
        event_id=1,
        dev_number="d1",
        alarm_name="test",
        value=1.0,
        limit=0.5,
        user_name="u",
        contact="openid",
        msg="msg",
    )


class _OkNotifier:
    name = "ok"

    async def send(self, n):
        return True


class _FailNotifier:
    name = "fail"

    async def send(self, n):
        return False


class _ExcNotifier:
    name = "exc"

    async def send(self, n):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_fan_out_all_success():
    r = await fan_out(_notif(), {"ok": _OkNotifier()})
    assert r == {"ok": True}


@pytest.mark.asyncio
async def test_fan_out_partial_failure():
    r = await fan_out(_notif(), {"ok": _OkNotifier(), "fail": _FailNotifier()})
    assert r["ok"] is True
    assert r["fail"] is False


@pytest.mark.asyncio
async def test_fan_out_exception_handled():
    r = await fan_out(_notif(), {"exc": _ExcNotifier()})
    assert r["exc"] is False


@pytest.mark.asyncio
async def test_fan_out_empty():
    r = await fan_out(_notif(), {})
    assert r == {}
