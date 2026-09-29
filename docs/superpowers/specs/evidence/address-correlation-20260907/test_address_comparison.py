import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "address_results", Path(__file__).with_name("analyze_address_results.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
compare = module.compare


def test_correct_shift_has_discriminating_matches():
    result = compare(list(range(27)), list(range(6, 27)), list(range(27)), 6, 6)
    assert result["counts"] == {"MATCH_DISCRIMINATING": 21}
    assert result["stable_prefix_support_count"] == 0
    assert result["status"] == "CONSISTENT_IN_THIS_OBSERVATION_ONLY"


def test_ignored_start_is_reported_as_observed_mismatch_not_firmware_fact():
    result = compare(list(range(27)), list(range(21)), list(range(27)), 6, 6)
    assert result["counts"] == {"MISMATCH": 21}
    assert result["stable_prefix_support_count"] == 21
    assert result["status"] == "ADDRESS_CORRELATION_INCONSISTENT"


def test_identical_values_are_not_discriminating():
    result = compare([0] * 27, [0] * 21, [0] * 27, 6, 6)
    assert result["status"] == "INCONCLUSIVE"
    assert result["counts"] == {"MATCH_UNDISCRIMINATING": 21}


def test_changed_reference_does_not_prove_wrong_address():
    result = compare([0] * 27, [123] * 21, [1] * 27, 6, 6)
    assert result["status"] == "INCONCLUSIVE"
    assert result["counts"] == {"DYNAMIC_REFERENCE": 21}
    assert result["stable_prefix_support_count"] == 0


def test_second_group_uses_offset_nine_and_physical_request_start():
    result = compare(list(range(18)), list(range(9, 18)), list(range(18)), 9, 27)
    assert result["rows"][0]["request_address"] == 27
    assert result["rows"][-1]["request_address"] == 35
    assert result["counts"] == {"MATCH_DISCRIMINATING": 9}


@pytest.mark.parametrize("offset", [-1, 7])
def test_invalid_comparison_geometry_is_rejected(offset):
    with pytest.raises(ValueError):
        compare([0] * 27, [0] * 21, [0] * 27, offset, 6)
