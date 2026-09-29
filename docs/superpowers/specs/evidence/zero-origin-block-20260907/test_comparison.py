"""Offline interpretation checks, with no serial or database capability."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "zero_origin_analysis", Path(__file__).with_name("analyze_results.py")
)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def test_distinguishes_raw_consistency_zero_dynamic_and_length_effect():
    result = analysis.compare([3, 0, 5, 6], [3, 0, 8, 9], [3, 0, 7, 6])
    assert result["counts"] == {
        "PREFIX_CONSISTENT_THIS_OBSERVATION": 1,
        "ZERO_UNINFORMATIVE": 1,
        "DYNAMIC_REFERENCE": 1,
        "LENGTH_INCONSISTENT": 1,
    }
    assert result["physical_identity_confirmed"] is False
    assert result["rows"][3]["response_position"] == 3


@pytest.mark.parametrize("args", [([], [], []), ([1], [1, 2], [1]), ([1], [1], [1, 2])])
def test_rejects_invalid_comparison_geometry(args):
    with pytest.raises(ValueError):
        analysis.compare(*args)


def test_ignores_whole_block_tail_outside_short_prefix():
    result = analysis.compare([3, 5], [3], [3, 9])
    assert result["counts"] == {"PREFIX_CONSISTENT_THIS_OBSERVATION": 1}


def test_equal_zero_is_not_identity_or_calibration():
    result = analysis.compare([0] * 36, [0] * 27, [0] * 36)
    assert result["counts"] == {"ZERO_UNINFORMATIVE": 27}
    assert result["physical_identity_confirmed"] is False
