"""统一错误码与业务异常基类。对应 spec §5.1 + §D.2。"""

from __future__ import annotations

from enum import IntEnum


class ErrCode(IntEnum):
    OK = 0
    BIZ_FAIL = -1  # HTTP 200
    BAD_PARAM = -100  # HTTP 400
    UNAUTHED = -101  # HTTP 401
    FORBIDDEN = -102  # HTTP 403
    DEV_OFFLINE = -200  # HTTP 200
    DEV_NO_REPLY = -201  # HTTP 200
    DEV_CRC_FAIL = -202  # HTTP 200
    INTERNAL = -300  # HTTP 500
    DB_UNAVAILABLE = -301  # HTTP 503
    PAY_SIGN_FAIL = -400  # HTTP 400 / 微信回调 xml_fail
    PAY_DUPLICATE = -401  # HTTP 200，幂等命中（非错误，事件登记）
    PAY_STATE_CONFLICT = -402  # HTTP 409 / 回调 xml_ok（非法状态转移）
    PAY_AMOUNT_MISMATCH = -403  # HTTP 409 / 回调 xml_ok（金额不符）
    PAY_EXPIRED = -404  # HTTP 409（订单已过期不可付）
    PAY_REFUND_FAIL = -405  # HTTP 502（退款第三方返回非 0）


_HTTP_MAP: dict[ErrCode, int] = {
    ErrCode.OK: 200,
    ErrCode.BIZ_FAIL: 200,
    ErrCode.BAD_PARAM: 400,
    ErrCode.UNAUTHED: 401,
    ErrCode.FORBIDDEN: 403,
    ErrCode.DEV_OFFLINE: 200,
    ErrCode.DEV_NO_REPLY: 200,
    ErrCode.DEV_CRC_FAIL: 200,
    ErrCode.INTERNAL: 500,
    ErrCode.DB_UNAVAILABLE: 503,
    ErrCode.PAY_SIGN_FAIL: 400,
    ErrCode.PAY_DUPLICATE: 200,
    ErrCode.PAY_STATE_CONFLICT: 409,
    ErrCode.PAY_AMOUNT_MISMATCH: 409,
    ErrCode.PAY_EXPIRED: 409,
    ErrCode.PAY_REFUND_FAIL: 502,
}


class BizError(Exception):
    """业务异常。api 层 FastAPI handler 捕获后转 ApiResponse。"""

    def __init__(self, code: ErrCode, msg: str) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg

    @property
    def http_status(self) -> int:
        return _HTTP_MAP[self.code]
