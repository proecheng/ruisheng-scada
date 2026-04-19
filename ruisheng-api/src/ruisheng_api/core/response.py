"""ApiResponse 统一包装（对应 spec §5.1）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from ruisheng_shared.errors.codes import ErrCode


class ApiResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    msg: str = "ok"
    data: Any = None
    transid: str | None = None


def ok(data: Any = None, transid: str | None = None) -> ApiResponse:
    return ApiResponse(code=ErrCode.OK.value, msg="ok", data=data, transid=transid)


def fail(code: ErrCode, msg: str, transid: str | None = None) -> ApiResponse:
    return ApiResponse(code=code.value, msg=msg, data=None, transid=transid)
