"""Simple threshold alarm — v2 C1.

No state machine (fired/reset pairing deferred to Plan 1.5).
Each out-of-range reading returns an AlarmEvent (caller dedup/rate-limit).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ruisheng_gw.domain.registry import ThresholdSpec


@dataclass(frozen=True)
class AlarmEvent:
    dev_number: str
    point_id: int
    value: float
    threshold: float
    level: int
    fired_at: float


def check_threshold(
    *,
    dev_number: str,
    point_id: int,
    value: float,
    spec: ThresholdSpec,
    now: float,
) -> AlarmEvent | None:
    if math.isnan(value):
        return None
    if spec.min_val is not None and value < spec.min_val:
        return AlarmEvent(
            dev_number=dev_number,
            point_id=point_id,
            value=value,
            threshold=spec.min_val,
            level=spec.alarm_level,
            fired_at=now,
        )
    if spec.max_val is not None and value > spec.max_val:
        return AlarmEvent(
            dev_number=dev_number,
            point_id=point_id,
            value=value,
            threshold=spec.max_val,
            level=spec.alarm_level,
            fired_at=now,
        )
    return None
