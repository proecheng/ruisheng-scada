"""ApiResponse 通用壳。"""

from __future__ import annotations

from ruisheng_shared.schemas.common import ApiResponse


def test_success_response() -> None:
    r = ApiResponse[dict](code=0, data={"x": 1})
    d = r.model_dump()
    assert d["code"] == 0
    assert d["msg"] == "ok"
    assert d["data"] == {"x": 1}


def test_transid_optional() -> None:
    r = ApiResponse[str](code=0, data="hello", transid="01HXXX")
    assert r.transid == "01HXXX"
