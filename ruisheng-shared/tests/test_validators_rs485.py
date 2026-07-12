"""Spec §A.8 — RS485 物理约束表：波特率 × 终端数 × 最小轮询周期。"""

from __future__ import annotations

import pytest
from ruisheng_shared.errors import BizError, ErrCode
from ruisheng_shared.validators.rs485 import (
    min_poll_interval_decisec,
    validate_bus_feasibility,
)


@pytest.mark.parametrize(
    ("baud", "device_count", "expected_min_decisec"),
    [
        (9600, 128, 60),  # 6s = 60 decisec
        (19200, 128, 30),  # 3s = 30 decisec
        (38400, 128, 20),  # 2s = 20 decisec
        (115200, 128, 10),  # 1s = 10 decisec
        (9600, 20, 10),  # 20 台 @ 9600 也能 1s
        (9600, 25, 10),  # exactly 1000ms still rounds to 1s
        (57600, 143, 20),  # 1001ms must round up to 2s
    ],
)
def test_min_poll_interval(baud: int, device_count: int, expected_min_decisec: int) -> None:
    assert min_poll_interval_decisec(baud, device_count) == expected_min_decisec


def test_validate_feasible() -> None:
    # 128 台 @ 9600 @ 6s → OK
    validate_bus_feasibility(baud=9600, device_count=128, min_decisec=60)


def test_validate_infeasible_raises() -> None:
    # 128 台 @ 9600 @ 1s → 不可行
    with pytest.raises(BizError) as exc_info:
        validate_bus_feasibility(baud=9600, device_count=128, min_decisec=10)
    assert exc_info.value.code.value == -100  # BAD_PARAM
    assert ">= 6.0s" in exc_info.value.msg


def test_unsupported_baud_raises_bad_param() -> None:
    with pytest.raises(BizError) as exc_info:
        min_poll_interval_decisec(12345, 1)

    assert exc_info.value.code is ErrCode.BAD_PARAM
    assert "12345" in exc_info.value.msg
