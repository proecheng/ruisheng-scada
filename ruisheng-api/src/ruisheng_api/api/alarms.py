"""Alarms API：/api/alarms/* + nested configs。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import alarms as alarms_repo
from ..db.repositories import devices as devices_repo
from ..deps import get_current_user, get_session
from .schemas.alarms import (
    AlarmCfgCreateRequest,
    AlarmCfgOut,
    AlarmCfgUpdateRequest,
)

cfg_router = APIRouter(prefix="/api/devices", tags=["alarms"])
record_router = APIRouter(prefix="/api/alarms", tags=["alarms"])


@cfg_router.get("/{dev_number}/alarms/configs", response_model=ApiResponse)
async def list_configs(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        rows = await alarms_repo.list_cfgs(session, dev_number)
    return ok(data={"items": [AlarmCfgOut.model_validate(c).model_dump() for c in rows]})


@cfg_router.post("/{dev_number}/alarms/configs", response_model=ApiResponse)
async def create_config(
    dev_number: str,
    body: AlarmCfgCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        c = await alarms_repo.create_cfg(
            session, dev_number=dev_number, **body.model_dump(exclude_none=True)
        )
        await session.execute(
            text("UPDATE devices SET update_flag = 1 WHERE dev_number = :d"),
            {"d": dev_number},
        )
    return ok(data=AlarmCfgOut.model_validate(c).model_dump())


@cfg_router.put("/{dev_number}/alarms/configs/{cfg_id}", response_model=ApiResponse)
async def update_config(
    dev_number: str,
    cfg_id: int,
    body: AlarmCfgUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields")
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        c = await alarms_repo.get_cfg(session, cfg_id)
        if c is None or c.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "cfg not found")
        await alarms_repo.update_cfg(session, c, updates)
        await session.execute(
            text("UPDATE devices SET update_flag = 1 WHERE dev_number = :d"),
            {"d": dev_number},
        )
    return ok(data=AlarmCfgOut.model_validate(c).model_dump())


@cfg_router.delete("/{dev_number}/alarms/configs/{cfg_id}", response_model=ApiResponse)
async def delete_config(
    dev_number: str,
    cfg_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        c = await alarms_repo.get_cfg(session, cfg_id)
        if c is None or c.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "cfg not found")
        await alarms_repo.delete_cfg(session, c)
        await session.execute(
            text("UPDATE devices SET update_flag = 1 WHERE dev_number = :d"),
            {"d": dev_number},
        )
    return ok(data={"deleted": cfg_id})


@record_router.get("", response_model=ApiResponse)
async def list_records(
    dev_number: str | None = Query(None),
    active_only: bool = Query(False),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows = await alarms_repo.list_records(
            session,
            dev_number=dev_number,
            active_only=active_only,
            from_ts=from_ts,
            to_ts=to_ts,
            offset=offset,
            limit=limit,
        )
    return ok(data={"items": rows})


@record_router.put("/{alarm_id}/reset", response_model=ApiResponse)
async def reset_alarm(
    alarm_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        if not await alarms_repo.reset_alarm(session, alarm_id):
            raise BizError(ErrCode.BAD_PARAM, "alarm not found or already reset")
    return ok(data={"alarm_id": alarm_id, "reset": True})
