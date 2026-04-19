"""Simple threshold alarm (v2 C1, no state machine)."""

from __future__ import annotations

from ruisheng_gw.domain.alarm_simple import check_threshold
from ruisheng_gw.domain.registry import ThresholdSpec


def _ts(min_val=None, max_val=None, level=1):
    return ThresholdSpec(min_val=min_val, max_val=max_val, alarm_level=level)


def test_no_threshold_returns_none() -> None:
    ev = check_threshold(
        dev_number="D",
        point_id=1,
        value=50.0,
        spec=_ts(None, None),
        now=100.0,
    )
    assert ev is None


def test_below_min_fires() -> None:
    ev = check_threshold(
        dev_number="D",
        point_id=1,
        value=-5.0,
        spec=_ts(min_val=0.0, max_val=100.0),
        now=100.0,
    )
    assert ev is not None
    assert ev.value == -5.0
    assert ev.threshold == 0.0
    assert ev.level == 1


def test_above_max_fires() -> None:
    ev = check_threshold(
        dev_number="D",
        point_id=1,
        value=150.0,
        spec=_ts(min_val=0.0, max_val=100.0),
        now=100.0,
    )
    assert ev is not None
    assert ev.threshold == 100.0


def test_in_range_returns_none() -> None:
    ev = check_threshold(
        dev_number="D",
        point_id=1,
        value=50.0,
        spec=_ts(min_val=0.0, max_val=100.0),
        now=100.0,
    )
    assert ev is None


def test_nan_value_returns_none() -> None:
    ev = check_threshold(
        dev_number="D",
        point_id=1,
        value=float("nan"),
        spec=_ts(min_val=0.0, max_val=100.0),
        now=100.0,
    )
    assert ev is None
