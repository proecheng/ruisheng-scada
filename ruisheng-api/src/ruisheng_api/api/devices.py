"""Devices API：/api/devices/*。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import devices as devices_repo
from ..deps import get_current_user, get_session
from .schemas.devices import (
    DeviceCreateRequest,
    DeviceOut,
    DeviceUpdateRequest,
)

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=ApiResponse)
async def list_devices(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    online_only: bool = Query(False),
    q: str | None = Query(None, max_length=100),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows, total = await devices_repo.list_devices(
            session, offset=offset, limit=limit, online_only=online_only, q=q
        )
    return ok(
        data={
            "total": total,
            "items": [DeviceOut.model_validate(d).model_dump() for d in rows],
        }
    )


@router.get("/{dev_number}", response_model=ApiResponse)
async def get_device(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
    if d is None:
        raise BizError(ErrCode.BAD_PARAM, "device not found")
    return ok(data=DeviceOut.model_validate(d).model_dump())


@router.post("", response_model=ApiResponse)
async def create_device(
    body: DeviceCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        existing = await devices_repo.get_by_dev_number(session, body.dev_number)
        if existing:
            raise BizError(ErrCode.BAD_PARAM, "dev_number already exists")
        d = await devices_repo.create_device(
            session, **body.model_dump(exclude_none=True), usr_group=user.usr_group
        )
    return ok(data=DeviceOut.model_validate(d).model_dump())


@router.put("/{dev_number}", response_model=ApiResponse)
async def update_device(
    dev_number: str,
    body: DeviceUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields to update")
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        await devices_repo.update_device_fields(session, d, updates)
        await session.execute(
            text("UPDATE devices SET update_flag = 1 WHERE dev_number = :d"),
            {"d": dev_number},
        )
    return ok(data=DeviceOut.model_validate(d).model_dump())


@router.delete("/{dev_number}", response_model=ApiResponse)
async def delete_device(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        await devices_repo.soft_delete(session, d)
    return ok(data={"deleted": dev_number})
