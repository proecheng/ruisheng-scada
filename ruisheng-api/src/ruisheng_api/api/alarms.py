"""Alarms API：/api/alarms/* + nested configs。"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from loguru import logger
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import alarms as alarms_repo
from ..db.repositories import devices as devices_repo
from ..db.repositories import points as points_repo
from ..deps import get_current_user, get_redis, get_session
from .schemas.alarms import (
    AlarmCfgCreateRequest,
    AlarmCfgOut,
    AlarmCfgUpdateRequest,
    AlarmSubscriptionCreateRequest,
)

cfg_router = APIRouter(prefix="/api/devices", tags=["alarms"])
record_router = APIRouter(prefix="/api/alarms", tags=["alarms"])


def _validate_alarm_rule(
    *,
    alarm_type: str,
    limit_value: float,
    relation_point_id: int | None,
    relation_alarm_type: str | None,
    relation_limit_value: float | None,
) -> None:
    def _valid_lx_count(kind: str | None, limit: float | None) -> bool:
        return kind != "LX" or (
            limit is not None and math.isfinite(limit) and limit > 0 and limit.is_integer()
        )

    if not math.isfinite(limit_value):
        raise BizError(ErrCode.BAD_PARAM, "limit must be finite")
    if not _valid_lx_count(alarm_type, limit_value):
        raise BizError(ErrCode.BAD_PARAM, "LX limit must be a positive integer")
    relation_present = (
        relation_point_id is not None,
        relation_alarm_type is not None,
        relation_limit_value is not None,
    )
    if any(relation_present) and not all(relation_present):
        raise BizError(ErrCode.BAD_PARAM, "relation point, alarm type and limit are all required")
    if relation_limit_value is not None and not math.isfinite(relation_limit_value):
        raise BizError(ErrCode.BAD_PARAM, "relation limit must be finite")
    if not _valid_lx_count(relation_alarm_type, relation_limit_value):
        raise BizError(ErrCode.BAD_PARAM, "relation LX limit must be a positive integer")


async def _mark_config_changed(session: AsyncSession, dev_number: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "UPDATE devices SET update_flag = update_flag + 1 "
                    "WHERE dev_number = :dev RETURNING update_flag"
                ),
                {"dev": dev_number},
            )
        ).scalar_one()
    )


async def _publish_config_changed(r: Any, dev_number: str, version: int) -> None:
    try:
        await r.publish(
            "channel:config:changed",
            json.dumps(
                {"dev_number": dev_number, "version": version},
                separators=(",", ":"),
            ),
        )
    except Exception:
        logger.exception("config change broadcast failed dev_number={}", dev_number)


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
    r: Any = Depends(get_redis),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    _validate_alarm_rule(
        alarm_type=body.alarm_type,
        limit_value=body.limit_value,
        relation_point_id=body.relation_point_id,
        relation_alarm_type=body.relation_alarm_type,
        relation_limit_value=body.relation_limit_value,
    )
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        d = await devices_repo.get_by_dev_number(session, dev_number)
        if d is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        point = await points_repo.get_point(session, body.point_id)
        if point is None or point.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "point not found for device")
        if body.relation_point_id is not None:
            relation_point = await points_repo.get_point(session, body.relation_point_id)
            if relation_point is None or relation_point.dev_number != dev_number:
                raise BizError(ErrCode.BAD_PARAM, "relation point not found for device")
        c = await alarms_repo.create_cfg(
            session, dev_number=dev_number, **body.model_dump(exclude_none=True)
        )
        config_version = await _mark_config_changed(session, dev_number)
    await _publish_config_changed(r, dev_number, config_version)
    return ok(data=AlarmCfgOut.model_validate(c).model_dump())


@cfg_router.put("/{dev_number}/alarms/configs/{cfg_id}", response_model=ApiResponse)
async def update_config(
    dev_number: str,
    cfg_id: int,
    body: AlarmCfgUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    r: Any = Depends(get_redis),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields")
    required_fields = {
        "alarm_name",
        "alarm_type",
        "limit_value",
        "enable",
        "phone_alarm",
        "reset_remind",
    }
    if any(updates.get(field) is None for field in required_fields & updates.keys()):
        raise BizError(ErrCode.BAD_PARAM, "required alarm fields cannot be null")
    if updates.get("relation_point_id", object()) is None:
        updates.update(
            relation_reg_bit=None,
            relation_alarm_type=None,
            relation_limit_value=None,
        )
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        c = await alarms_repo.get_cfg(session, cfg_id)
        if c is None or c.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "cfg not found")
        effective_relation_point_id = updates.get("relation_point_id", c.relation_point_id)
        effective_relation_alarm_type = updates.get("relation_alarm_type", c.relation_alarm_type)
        effective_relation_limit_value = updates.get("relation_limit_value", c.relation_limit_value)
        _validate_alarm_rule(
            alarm_type=str(updates.get("alarm_type", c.alarm_type)),
            limit_value=float(updates.get("limit_value", c.limit_value)),
            relation_point_id=(
                int(effective_relation_point_id)
                if effective_relation_point_id is not None
                else None
            ),
            relation_alarm_type=(
                str(effective_relation_alarm_type)
                if effective_relation_alarm_type is not None
                else None
            ),
            relation_limit_value=(
                float(effective_relation_limit_value)
                if effective_relation_limit_value is not None
                else None
            ),
        )
        if effective_relation_point_id is not None:
            relation_point = await points_repo.get_point(session, int(effective_relation_point_id))
            if relation_point is None or relation_point.dev_number != dev_number:
                raise BizError(ErrCode.BAD_PARAM, "relation point not found for device")
        await alarms_repo.update_cfg(session, c, updates)
        config_version = await _mark_config_changed(session, dev_number)
    await _publish_config_changed(r, dev_number, config_version)
    return ok(data=AlarmCfgOut.model_validate(c).model_dump())


@cfg_router.delete("/{dev_number}/alarms/configs/{cfg_id}", response_model=ApiResponse)
async def delete_config(
    dev_number: str,
    cfg_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    r: Any = Depends(get_redis),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        c = await alarms_repo.get_cfg(session, cfg_id)
        if c is None or c.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "cfg not found")
        await alarms_repo.delete_cfg(session, c)
        config_version = await _mark_config_changed(session, dev_number)
    await _publish_config_changed(r, dev_number, config_version)
    return ok(data={"deleted": cfg_id})


def _check_notification_admin(user: CurrentUser) -> None:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)


async def _notification_tenant(
    session: AsyncSession,
    *,
    cfg_id: int,
    dev_number: str,
    current_tenant: str,
) -> str:
    cfg = await alarms_repo.get_cfg(session, cfg_id)
    device = await devices_repo.get_by_dev_number(session, dev_number)
    if cfg is None or cfg.dev_number != dev_number or device is None:
        raise BizError(ErrCode.BAD_PARAM, "cfg not found")
    if str(device.usr_group) != current_tenant:
        raise BizError(ErrCode.FORBIDDEN, "notification management is tenant-local")
    return current_tenant


@cfg_router.get(
    "/{dev_number}/alarms/configs/{cfg_id}/subscriptions",
    response_model=ApiResponse,
)
async def list_subscriptions(
    dev_number: str,
    cfg_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    _check_notification_admin(user)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        tenant = await _notification_tenant(
            session,
            cfg_id=cfg_id,
            dev_number=dev_number,
            current_tenant=user.usr_group,
        )
        rows = await alarms_repo.list_subscriptions(session, cfg_id, tenant)
    return ok(data={"items": rows})


@cfg_router.post(
    "/{dev_number}/alarms/configs/{cfg_id}/subscriptions",
    response_model=ApiResponse,
)
async def create_subscription(
    dev_number: str,
    cfg_id: int,
    body: AlarmSubscriptionCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    _check_notification_admin(user)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        tenant = await _notification_tenant(
            session,
            cfg_id=cfg_id,
            dev_number=dev_number,
            current_tenant=user.usr_group,
        )
        target = await session.execute(
            text(
                "SELECT 1 FROM users WHERE user_name = :user "
                "AND usr_group = :tenant AND deleted_at IS NULL"
            ),
            {"user": body.user_name, "tenant": tenant},
        )
        if target.scalar_one_or_none() is None:
            raise BizError(ErrCode.BAD_PARAM, "user not found")
        row = await alarms_repo.create_subscription(
            session,
            cfg_id=cfg_id,
            user_name=body.user_name,
            channel=body.channel,
            usr_group=tenant,
        )
    return ok(data=row)


@cfg_router.delete(
    "/{dev_number}/alarms/configs/{cfg_id}/subscriptions/{subscription_id}",
    response_model=ApiResponse,
)
async def delete_subscription(
    dev_number: str,
    cfg_id: int,
    subscription_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    _check_notification_admin(user)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        tenant = await _notification_tenant(
            session,
            cfg_id=cfg_id,
            dev_number=dev_number,
            current_tenant=user.usr_group,
        )
        if not await alarms_repo.delete_subscription(session, cfg_id, subscription_id, tenant):
            raise BizError(ErrCode.BAD_PARAM, "subscription not found")
    return ok(data={"deleted": subscription_id})


@cfg_router.get(
    "/{dev_number}/alarms/configs/{cfg_id}/delivery-audit",
    response_model=ApiResponse,
)
async def delivery_audit(
    dev_number: str,
    cfg_id: int,
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    _check_notification_admin(user)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        tenant = await _notification_tenant(
            session,
            cfg_id=cfg_id,
            dev_number=dev_number,
            current_tenant=user.usr_group,
        )
        rows = await alarms_repo.list_delivery_audit(session, cfg_id, tenant, limit=limit)
    return ok(data={"items": rows})


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
