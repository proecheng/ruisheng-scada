"""Control API：POST /api/devices/{dev_number}/control。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import control as control_repo
from ..deps import get_current_user, get_redis, get_session
from .do_control import router as do_control_router
from .schemas.control import ControlAction, ControlActionPreset

if TYPE_CHECKING:
    from typing import Any

    import redis.asyncio as redis_async

router = APIRouter(prefix="/api/devices", tags=["control"])
router.include_router(do_control_router)
query_router = APIRouter(prefix="/api/control", tags=["control"])

CONTROL_ACTION_PRESETS: tuple[ControlActionPreset, ...] = (
    ControlActionPreset(
        key="start",
        label="启动",
        fun_code=6,
        reg=0,
        value=1,
        description="写单个保持寄存器：寄存器 0 = 1",
    ),
    ControlActionPreset(
        key="stop",
        label="停止",
        fun_code=6,
        reg=0,
        value=0,
        high_risk=True,
        description="写单个保持寄存器：寄存器 0 = 0",
    ),
    ControlActionPreset(
        key="reset",
        label="复位",
        fun_code=6,
        reg=1,
        value=1,
        high_risk=True,
        description="写单个保持寄存器：寄存器 1 = 1",
    ),
)


@query_router.get("/actions", response_model=ApiResponse)
async def list_control_actions(
    user: CurrentUser = Depends(get_current_user),
) -> ApiResponse:
    check_ca(user, bit=0x01)
    return ok(data={"items": [a.model_dump() for a in CONTROL_ACTION_PRESETS]})


@router.post("/{dev_number}/control", response_model=ApiResponse)
async def control(
    dev_number: str,
    body: ControlAction,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    otp_code: str | None = Header(default=None, alias="X-OTP-Code"),
    user: CurrentUser = Depends(get_current_user),
    r: redis_async.Redis[Any] = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_ca(user, bit=0x01)  # 控制权
    # The gateway worker only executes do_v1. Recording a generic register write
    # and returning pending tells the operator a command was sent.
    raise BizError(
        ErrCode.BAD_PARAM,
        "generic register writes are not executed; use per-channel DO control",
    )


@query_router.get("/commands", response_model=ApiResponse)
async def list_commands(
    user_name: str | None = None,
    dev_number: str | None = None,
    offset: int = 0,
    limit: int = 50,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    effective_user = (
        user_name if user.role in ("Administrators", "GroupCompany", "Company") else user.user_name
    )
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows = await control_repo.list_actions(
            session,
            user_name=effective_user,
            dev_number=dev_number,
            offset=offset,
            limit=limit,
        )
    return ok(data={"items": rows})


@query_router.delete("/commands/{cmd_id}", response_model=ApiResponse)
async def cancel_command(
    cmd_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_ca(user, bit=0x01)
    owner_filter = (
        None if user.role in ("Administrators", "GroupCompany", "Company") else user.user_name
    )
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        cancelled = await control_repo.cancel_action(session, cmd_id, user_name=owner_filter)
    if not cancelled:
        raise BizError(ErrCode.BAD_PARAM, "cmd not pending or not found")
    return ok(data={"cmd_id": cmd_id, "status": "cancelled"})
