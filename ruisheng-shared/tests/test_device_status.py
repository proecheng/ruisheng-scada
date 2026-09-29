import pytest
from ruisheng_shared.device_status import serial_online_timeout_seconds, serial_response_is_recent


@pytest.mark.parametrize("interval,grace", [(10, 10), (50, 15), (1000, 300)])
def test_expiry_boundary_and_clock_reversal(interval, grace):
    assert serial_online_timeout_seconds(interval) == grace
    assert serial_response_is_recent(
        last_back=1000, now=1000 + grace - 0.001, interval_decisec=interval
    )
    for last_back, now in [(0, 1), (1000, 999), (1000, 1000 + grace)]:
        assert not serial_response_is_recent(
            last_back=last_back, now=now, interval_decisec=interval
        )
