"""告警判定类型。对应 spec §F + DB CHECK 约束（§4.2 device_waring_cfgs）。"""

from __future__ import annotations

from enum import StrEnum


class AlarmType(StrEnum):
    """5 种告警判定类型。值直接存 DB，故用字符串常量。"""

    GT = ">"
    LT = "<"
    EQ = "="
    NE = "!="
    LX = "LX"  # 连续 N 次越限
