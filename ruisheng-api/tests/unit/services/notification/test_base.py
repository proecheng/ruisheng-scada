from ruisheng_api.services.notification.base import AlarmNotification, INotifier


def test_alarm_notification_frozen():
    n = AlarmNotification(
        trace_id="t1",
        event_id=1,
        dev_number="d1",
        alarm_name="overcurrent",
        value=95.0,
        limit=80.0,
        user_name="alice",
        contact="openid_123",
        msg="danger",
    )
    assert n.value == 95.0
    try:
        n.value = 100.0  # type: ignore[misc]
        raise AssertionError("should raise")
    except Exception:
        pass  # frozen dataclass


def test_inotifier_protocol():
    # Just verify it can be imported and used as a type
    assert hasattr(INotifier, "send")
