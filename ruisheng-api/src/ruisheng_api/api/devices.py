"""Devices API：/api/devices/*。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import devices as devices_repo
from ..db.repositories import timeseries as ts_repo
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
        if body.transport_type == "serial" and body.serial_port is not None:
            endpoint = await devices_repo.get_serial_endpoint(
                session,
                serial_port=body.serial_port,
                modbus_addr=body.modbus_addr,
            )
            if endpoint is not None:
                raise BizError(ErrCode.BAD_PARAM, "serial_port and modbus_addr already in use")
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
    if body.transport_type == "tcp":
        updates["serial_port"] = None
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields to update")
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        if (
            body.serial_port is not None
            and body.transport_type is None
            and d.transport_type != "serial"
        ):
            raise BizError(ErrCode.BAD_PARAM, "serial_port can only be set on serial devices")
        target_transport = updates.get("transport_type", d.transport_type)
        target_serial_port = updates.get("serial_port", d.serial_port)
        target_modbus_addr = d.modbus_addr
        if target_transport == "serial" and isinstance(target_serial_port, str):
            endpoint = await devices_repo.get_serial_endpoint(
                session,
                serial_port=target_serial_port,
                modbus_addr=target_modbus_addr,
            )
            if endpoint is not None and endpoint.dev_number != dev_number:
                raise BizError(ErrCode.BAD_PARAM, "serial_port and modbus_addr already in use")
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


@router.get("/{dev_number}/realtime", response_model=ApiResponse)
async def realtime(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        rows = await ts_repo.load_realtime(session, dev_number)
    return ok(data={"dev_number": dev_number, "points": rows})


@router.get("/{dev_number}/history", response_model=ApiResponse)
async def history(
    dev_number: str,
    response: Response,
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
    point_id: int | None = Query(None),
    sample_interval_s: int | None = Query(None, ge=1, le=86_400),
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=ts_repo.MAX_HISTORY_ROWS),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    if to_ts <= from_ts:
        raise BizError(ErrCode.BAD_PARAM, "to must be > from")
    duration = int((to_ts - from_ts).total_seconds())
    interval = sample_interval_s or ts_repo.pick_sample_interval(duration)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        rows = await ts_repo.load_history(
            session,
            dev_number=dev_number,
            point_id=point_id,
            from_ts=from_ts,
            to_ts=to_ts,
            sample_interval_s=interval,
            offset=offset,
            limit=limit,
        )
    if interval > 1:
        response.headers["X-Downsampled"] = "true"
        response.headers["X-Sample-Interval-S"] = str(interval)
    return ok(
        data={"rows": rows, "next_offset": offset + len(rows) if len(rows) == limit else None}
    )
