"""Point dataclass + scaling (raw → engineering → display).

engineering = raw * point_ratio + point_offset
display     = engineering * user_ratio + user_point_offset

Edge cases (v2 I3):
- point_ratio=0.0 allowed (constant = offset); safe
- raw=NaN → engineering/display = NaN (propagated)
- raw=+/-inf → ScalingError
- overflow to inf during multiply → ScalingError
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class ScalingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Point:
    point_id: int
    dev_number: str
    point_ratio: float
    point_offset: float
    user_ratio: float
    user_point_offset: float


def apply_scaling(p: Point, *, raw: float) -> tuple[float, float]:
    if math.isnan(raw):
        return float("nan"), float("nan")
    if math.isinf(raw):
        raise ScalingError(f"raw is infinite for point {p.point_id}")
    eng = raw * p.point_ratio + p.point_offset
    if math.isinf(eng):
        raise ScalingError(f"engineering overflow for point {p.point_id}")
    disp = eng * p.user_ratio + p.user_point_offset
    if math.isinf(disp):
        raise ScalingError(f"display overflow for point {p.point_id}")
    return eng, disp
