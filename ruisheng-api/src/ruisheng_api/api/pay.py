"""Pay API：/api/pay/* + /wechat/pay/notify。"""

from __future__ import annotations

from datetime import UTC, datetime

import ulid
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text as _t
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import pay as pay_repo
from ..deps import get_current_user, get_session
from .schemas.pay import CreateOrderRequest

pay_router = APIRouter(prefix="/api/pay", tags=["pay"])
notify_router = APIRouter(tags=["pay"])

_NOTIFY_TIME_WINDOW_SEC = 300


@pay_router.post("/orders", response_model=ApiResponse)
async def create_order(
    body: CreateOrderRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    out_trade_no = str(ulid.ULID())
    usr_group = body.usr_group or user.usr_group
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        # MVP stub: real WeChat unified-order call goes here
        order = await pay_repo.create_pay_order(
            session,
            out_trade_no=out_trade_no,
            openid=body.openid,
            total_fee=body.amount_fen,
            description=body.description,
            usr_group=usr_group,
        )
    return ok(data={**order, "out_trade_no": out_trade_no, "prepay_stub": "todo"})


@pay_router.get("/orders", response_model=ApiResponse)
async def list_orders(
    openid: str | None = Query(None),
    status: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows = await pay_repo.list_pay_orders(session, openid=openid, status=status)
    return ok(data={"items": rows})


@pay_router.get("/orders/{out_trade_no}", response_model=ApiResponse)
async def get_order(
    out_trade_no: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        order = await pay_repo.get_pay_order(session, out_trade_no)
    if order is None:
        raise BizError(ErrCode.BAD_PARAM, "order not found")
    return ok(data=order)


def _xml_ok() -> Response:
    return Response(
        content="<xml><return_code><![CDATA[SUCCESS]]></return_code></xml>",
        media_type="application/xml",
    )


def _xml_fail(msg: str) -> Response:
    return Response(
        content=(
            f"<xml><return_code><![CDATA[FAIL]]></return_code>"
            f"<return_msg><![CDATA[{msg}]]></return_msg></xml>"
        ),
        media_type="application/xml",
    )


def _parse_notify_fields(
    raw: dict[str, str],
    api_key: str,
) -> tuple[str, int, datetime]:
    """Parse and validate notify form fields.

    Raises ValueError on any validation failure — caller wraps with _xml_fail.
    """
    from ..services.wechat_pay import verify_sign

    received = raw.pop("sign", "") or ""
    if not received:
        raise ValueError("missing sign")
    if not verify_sign(raw, received, api_key):
        raise ValueError("bad signature")
    out_trade_no = str(raw.get("out_trade_no") or "")
    if not out_trade_no:
        raise ValueError("missing fields")
    total_fee = int(str(raw.get("total_fee") or ""))
    if total_fee < 0:
        raise ValueError("invalid fee")
    time_end_raw = str(raw.get("time_end") or "")
    te = datetime.strptime(time_end_raw, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if abs((datetime.now(UTC) - te).total_seconds()) > _NOTIFY_TIME_WINDOW_SEC:
        raise ValueError("timestamp out of window")
    return out_trade_no, total_fee, te


@notify_router.post("/wechat/pay/notify")
async def wechat_pay_notify(request: Request) -> Response:
    cfg = request.app.state.config
    api_key = getattr(cfg, "wechat_api_v3_key", "") or ""
    if not api_key:
        return _xml_fail("not configured")
    raw_form = dict((await request.form()).items())
    # form values are str (or UploadFile); cast all to str
    raw: dict[str, str] = {k: str(v) for k, v in raw_form.items()}
    try:
        out_trade_no, total_fee, te = _parse_notify_fields(raw, api_key)
    except (ValueError, KeyError) as exc:
        return _xml_fail(str(exc))
    gw_factory = request.app.state.gw_session_factory
    async with gw_factory() as session, session.begin():
        res = await session.execute(
            _t(
                "INSERT INTO pay_orders_seen (out_trade_no, notified_at) "
                "VALUES (:o, now()) ON CONFLICT (out_trade_no) DO NOTHING"
            ),
            {"o": out_trade_no},
        )
        if (res.rowcount or 0) == 0:
            return _xml_ok()
        try:
            await session.execute(
                _t(
                    "UPDATE pay_orders "
                    "SET pay_state='paid', paid_at=:t, updated_at=now() "
                    "WHERE out_trade_no=:o AND pay_state='pending' AND total_fee=:f"
                ),
                {"o": out_trade_no, "t": te, "f": total_fee},
            )
        except Exception:
            from loguru import logger

            logger.exception("wxpay mark_paid failed")
    return _xml_ok()
