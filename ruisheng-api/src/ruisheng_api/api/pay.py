"""Pay API：/api/pay/* + /wechat/pay/notify。"""

from __future__ import annotations

import ulid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import pay as pay_repo
from ..deps import get_current_user, get_session
from .schemas.pay import CreateOrderRequest

pay_router = APIRouter(prefix="/api/pay", tags=["pay"])
notify_router = APIRouter(tags=["pay"])


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
