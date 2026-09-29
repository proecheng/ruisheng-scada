import pytest
from pydantic import ValidationError
from ruisheng_api.api.points import _parse_csv_row, _point_to_csv_row
from ruisheng_api.api.schemas.points import PointCreateRequest, PointOut, PointUpdateRequest


def request(**updates):
    return PointCreateRequest.model_validate(
        {"point_name": "DI", "point_number": 0, "fun_code": 3, "dev_addr": 1, "value_type": "字"}
        | updates
    )


@pytest.mark.parametrize("width", [1, 2, 4, 8, 16])
def test_configurable_register_channel_count(width):
    assert request(display_bits=width).display_bits == width


@pytest.mark.parametrize("width", [0, 17, -1, 1.5])
def test_reject_out_of_range_width(width):
    with pytest.raises(ValidationError):
        request(display_bits=width)
    with pytest.raises(ValidationError):
        PointUpdateRequest(display_bits=width)


@pytest.mark.parametrize("value_type", ["有符号字节", "无符号字节"])
def test_full_word_byte_types_are_complete_registers(value_type):
    created = request(value_type=value_type)
    assert created.value_type == value_type
    with pytest.raises(ValidationError):
        request(value_type=value_type, display_bits=2)


@pytest.mark.parametrize(
    "updates",
    [
        {"value_type": "bit", "r_bit": 0},
        {"value_type": "双字"},
        {"value_type": "bit", "fun_code": 1},
        {"point_ratio": 0.1},
        {"user_ratio": 10},
        {"point_offset": 1},
        {"user_point_offset": -1},
    ],
)
def test_bit_group_requires_unscaled_complete_register(updates):
    with pytest.raises(ValidationError):
        request(display_bits=2, **updates)


def test_legacy_csv_and_display_csv_round_trip():
    base = request()
    point = PointOut.model_validate(dict(id=1, dev_number="D1", **base.model_dump()))
    legacy = _point_to_csv_row(point)
    legacy.pop("display_bits")
    assert _parse_csv_row(legacy).display_bits is None
    binary = PointOut.model_validate(
        dict(id=1, dev_number="D1", **request(display_bits=8).model_dump())
    )
    assert _parse_csv_row(_point_to_csv_row(binary)).display_bits == 8
    assert PointUpdateRequest(display_bits=None).model_dump(exclude_unset=True) == {
        "display_bits": None
    }
