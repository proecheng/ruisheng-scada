"""微信支付签名算法（spec §3.5）+ 客户端 stub。"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping


def wechat_pay_sign(params: Mapping[str, object], api_v3_key: str) -> str:
    """HMAC-SHA256 sign. V2 spec: sorted key=val pairs + &key=<api_v3_key>"""
    items = sorted((k, str(v)) for k, v in params.items() if v is not None and v != "")
    joined = "&".join(f"{k}={v}" for k, v in items) + f"&key={api_v3_key}"
    mac = hmac.new(api_v3_key.encode(), joined.encode(), hashlib.sha256)
    return mac.hexdigest().upper()


def verify_sign(params: Mapping[str, object], received: str, api_v3_key: str) -> bool:
    return hmac.compare_digest(
        received.upper(),
        wechat_pay_sign({k: v for k, v in params.items() if k != "sign"}, api_v3_key),
    )
