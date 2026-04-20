"""RS485 总线物理可行性校验。对应 spec §A.8。

约束表来源：单帧往返 ≈ (请求 8B + 响应 25B) * 10 bit / baud_rate + 帧间静止 4ms

波特率     单次往返    128 台一轮    最小轮询周期
9600        40 ms      5.1s          6s
19200       20 ms      2.6s          3s
38400       10 ms      1.3s          2s
115200       4 ms      0.5s          1s
"""

from __future__ import annotations

from ruisheng_shared.errors import BizError, ErrCode

# 保守 RTT 单位：ms
_RTT_MS: dict[int, int] = {
    9600: 40,
    19200: 20,
    38400: 10,
    57600: 7,
    115200: 4,
}


def min_poll_interval_decisec(baud: int, device_count: int) -> int:
    """给定波特率与总线设备数，返回物理上可行的最小轮询周期（0.1s 单位）。

    算法：ceil(one_round_ms / 1000) 秒 → decisec，并保证下限为 10（即 1s）。
    """
    if baud not in _RTT_MS:
        raise BizError(ErrCode.BAD_PARAM, f"波特率 {baud} 不在支持表中")
    one_round_ms = _RTT_MS[baud] * max(device_count, 1)
    # ms → 向上取整到 1s → 转 decisec (×10)；下限 1s
    return max(((one_round_ms + 999) // 1000) * 10, 10)


def validate_bus_feasibility(*, baud: int, device_count: int, min_decisec: int) -> None:
    """若用户配置的最小轮询周期 < 物理下限，抛 BizError(BAD_PARAM)。"""
    physical = min_poll_interval_decisec(baud, device_count)
    if min_decisec < physical:
        hint_s = physical / 10.0
        raise BizError(
            ErrCode.BAD_PARAM,
            f"该波特率 ({baud} bps) 下 {device_count} 台终端的最小轮询周期应 >= {hint_s:.1f}s",
        )
