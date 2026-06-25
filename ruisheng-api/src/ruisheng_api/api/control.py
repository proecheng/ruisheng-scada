"""Control API：POST /api/devices/{dev_number}/control。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import ulid
from fastapi import APIRouter, Depends, Header
from loguru import logger
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import control as control_repo
from ..db.repositories import devices as devices_repo
from ..deps import get_current_user, get_redis, get_session
from ..pubsub.publisher import xadd_control_cmd
from ..services import otp as otp_svc
from .schemas.control import ControlAction, ControlActionPreset, ControlResponseData

if TYPE_CHECKING:
    from typing import Any

    import redis.asyncio as redis_async

router = APIRouter(prefix="/api/devices", tags=["control"])
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


def _action_key(a: ControlAction) -> str:
    return hashlib.sha256(f"{a.fun_code}:{a.reg}:{a.value}".encode()).hexdigest()[:16]


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
    if body.high_risk:
        check_ca(user, bit=0x04)
        if not otp_code or not await otp_svc.verify_otp(
            r, action="control", key=user.user_name, code=otp_code
        ):
            raise BizError(ErrCode.UNAUTHED, "OTP required for high-risk op")

    if idempotency_key:
        cached = await r.get(f"idempotency:control:{user.user_name}:{idempotency_key}")
        if cached:
            return ok(data={"cmd_id": cached, "status": "duplicate"})

    cmd_id = str(ulid.ULID())
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        if await control_repo.check_recent_duplicate(
            session,
            user_name=user.user_name,
            dev_number=dev_number,
            action_key=_action_key(body),
        ):
            raise BizError(ErrCode.BIZ_FAIL, "duplicate action within 5s")
        action_dict: dict[str, object] = {**body.model_dump(), "action_key": _action_key(body)}
        await control_repo.insert_action(
            session,
            dev_number=dev_number,
            user_name=user.user_name,
            cmd_id=cmd_id,
            action=action_dict,
            usr_group=user.usr_group,
        )

    await xadd_control_cmd(
        r,
        cmd_id=cmd_id,
        payload={
            "dev_number": dev_number,
            "user_name": user.user_name,
            **body.model_dump(),
        },
    )

    if idempotency_key:
        await r.setex(f"idempotency:control:{user.user_name}:{idempotency_key}", 86400, cmd_id)

    logger.bind(cmd_id=cmd_id, dev_number=dev_number, user_name=user.user_name).info(
        "control cmd dispatched"
    )
    return ok(
        data=ControlResponseData(
            cmd_id=cmd_id,
            status="pending",
            acted_at=datetime.now(UTC),
        ).model_dump(mode="json")
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
