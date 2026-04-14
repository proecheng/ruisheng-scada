"""Spec §5.1 — ErrCode 枚举 + BizError 异常基类。"""
from __future__ import annotations

import pytest

from ruisheng_shared.errors import BizError, ErrCode


def test_errcode_values() -> None:
    assert ErrCode.OK == 0
    assert ErrCode.BIZ_FAIL == -1
    assert ErrCode.BAD_PARAM == -100
    assert ErrCode.UNAUTHED == -101
    assert ErrCode.FORBIDDEN == -102
    assert ErrCode.DEV_OFFLINE == -200
    assert ErrCode.DEV_NO_REPLY == -201
    assert ErrCode.DEV_CRC_FAIL == -202
    assert ErrCode.INTERNAL == -300
    assert ErrCode.DB_UNAVAILABLE == -301


def test_biz_error_carries_code() -> None:
    exc = BizError(ErrCode.DEV_OFFLINE, "设备 60270012 离线")
    assert exc.code is ErrCode.DEV_OFFLINE
    assert exc.http_status == 200
    assert str(exc) == "设备 60270012 离线"


def test_biz_error_http_status_map() -> None:
    assert BizError(ErrCode.BAD_PARAM, "").http_status == 400
    assert BizError(ErrCode.UNAUTHED, "").http_status == 401
    assert BizError(ErrCode.FORBIDDEN, "").http_status == 403
    assert BizError(ErrCode.INTERNAL, "").http_status == 500
    assert BizError(ErrCode.DB_UNAVAILABLE, "").http_status == 503
    assert BizError(ErrCode.DEV_OFFLINE, "").http_status == 200


def test_unknown_code_rejected() -> None:
    with pytest.raises(ValueError):
        ErrCode(-999)
