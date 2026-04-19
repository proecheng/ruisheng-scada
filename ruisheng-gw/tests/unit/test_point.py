"""Point scaling: raw → engineering → display, with ratio/offset + edge cases."""

from __future__ import annotations

import math

import pytest
from ruisheng_gw.domain.point import Point, ScalingError, apply_scaling


def _p(**kw):
    defaults = {
        "point_id": 1,
        "dev_number": "DEV-001",
        "point_ratio": 1.0,
        "point_offset": 0.0,
        "user_ratio": 1.0,
        "user_point_offset": 0.0,
    }
    defaults.update(kw)
    return Point(**defaults)


def test_identity_scaling() -> None:
    eng, disp = apply_scaling(_p(), raw=42)
    assert eng == 42.0 and disp == 42.0


def test_engineering_ratio_and_offset() -> None:
    p = _p(point_ratio=0.1, point_offset=10.0)
    eng, _ = apply_scaling(p, raw=100)
    assert eng == pytest.approx(20.0)  # 100*0.1 + 10


def test_user_scaling_on_top_of_engineering() -> None:
    p = _p(point_ratio=0.1, point_offset=0.0, user_ratio=2.0, user_point_offset=1.0)
    eng, disp = apply_scaling(p, raw=100)
    assert eng == pytest.approx(10.0)
    assert disp == pytest.approx(21.0)  # 10*2 + 1


def test_zero_point_ratio_raises() -> None:
    """point_ratio=0 → engineering = offset only (never divides); valid."""
    p = _p(point_ratio=0.0, point_offset=5.0)
    eng, _ = apply_scaling(p, raw=42)
    assert eng == 5.0


def test_nan_input_returns_nan() -> None:
    p = _p()
    eng, disp = apply_scaling(p, raw=float("nan"))
    assert math.isnan(eng) and math.isnan(disp)


def test_infinity_raises_scaling_error() -> None:
    p = _p()
    with pytest.raises(ScalingError):
        apply_scaling(p, raw=float("inf"))
