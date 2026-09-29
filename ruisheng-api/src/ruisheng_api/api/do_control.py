"""Per-device, explicitly configured DO operations and durable result lookup."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis_async
import ulid
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from redis.exceptions import RedisError
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import control as control_repo
from ..db.repositories import devices as devices_repo
from ..deps import get_current_user, get_redis, get_session
from ..pubsub.publisher import xadd_control_cmd
from ..services import otp as otp_svc

router = APIRouter()
PROFILE = "two_channel_fc05_00ff_v1"
logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    _Redis = redis_async.Redis[Any]
else:
    _Redis = redis_async.Redis


class DoChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int = Field(ge=1, le=2, strict=True)
    high: StrictBool


class DoAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channels: list[DoChannel] = Field(min_length=1, max_length=2)
    config_version: int = Field(ge=0, strict=True)
    high_risk: bool = False

    @model_validator(mode="after")
    def unique_channels(self) -> DoAction:
        if len({c.number for c in self.channels}) != len(self.channels):
            raise ValueError("duplicate DO channel")
        return self


async def profile_enabled(session: AsyncSession, dev_number: str) -> bool:
    result = await session.execute(
        text("""SELECT base_msg_value FROM device_static_data
             WHERE dev_number=:d AND base_msg_name='do_control_profile' ORDER BY id DESC LIMIT 1"""),
        {"d": dev_number},
    )
    return result.scalar_one_or_none() == PROFILE


@router.get("/{dev_number}/do-control", response_model=ApiResponse)
async def get_do_control(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_ca(user, bit=0x01)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        device = await devices_repo.get_by_dev_number(session, dev_number)
        if device is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        enabled = (
            device.transport_type == "serial"
            and device.read_profile == "zero_origin_38"
            and await profile_enabled(session, dev_number)
        )
        row = (
            (
                await session.execute(
                    text(  # noqa: TNL001 (device visibility checked under transaction-local RLS above)
                        """
            SELECT r.org_value, r.recorded_at FROM point_data_realtime r
            JOIN device_points p ON p.id=r.point_id AND p.dev_number=r.dev_number
            WHERE r.dev_number=:d AND p.point_number=1 AND p.fun_code=3
              AND p.value_type='字' AND p.r_bit IS NULL ORDER BY p.id LIMIT 1
        """
                    ),
                    {"d": dev_number},
                )
            )
            .mappings()
            .first()
        )
        return ok(
            data={
                "supported": enabled,
                "config_version": device.update_flag,
                "channels": [1, 2] if enabled else [],
                "enabled": device.is_enabled,
                "sample": dict(row) if row else None,
            }
        )


@router.post("/{dev_number}/do-control", response_model=ApiResponse)
async def set_do_control(
    dev_number: str,
    body: DoAction,
    otp_code: str | None = Header(default=None, alias="X-OTP-Code"),
    user: CurrentUser = Depends(get_current_user),
    r: _Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_ca(user, bit=0x01)
    if body.high_risk:
        check_ca(user, bit=0x04)
        if not otp_code or not await otp_svc.verify_otp(
            r, action="control", key=user.user_name, code=otp_code
        ):
            raise BizError(ErrCode.UNAUTHED, "OTP required for high-risk op")
    mask = sum(1 << (c.number - 1) for c in body.channels)
    value = sum(1 << (c.number - 1) for c in body.channels if c.high)
    cmd_id = str(ulid.ULID())
    action: dict[str, object] = {
        "kind": "do_v1",
        "profile": PROFILE,
        "mask": mask,
        "value": value,
        "config_version": body.config_version,
        "high_risk": body.high_risk,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=15)).isoformat(),
        "action_key": hashlib.sha256(f"{PROFILE}:{mask}:{value}".encode()).hexdigest()[:16],
    }
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        device = await devices_repo.get_by_dev_number(session, dev_number, for_update=True)
        if device is None:
            raise BizError(ErrCode.BAD_PARAM, "device not found")
        if not (
            device.is_enabled
            and device.transport_type == "serial"
            and device.read_profile == "zero_origin_38"
            and await profile_enabled(session, dev_number)
        ):
            raise BizError(ErrCode.BAD_PARAM, "DO control is not configured for this device")
        if device.update_flag != body.config_version:
            raise BizError(ErrCode.BIZ_FAIL, "device configuration changed; refresh and retry")
        pending = await session.execute(
            text(  # noqa: TNL001 (transaction-local API tenant RLS is applied above)
                """SELECT 1 FROM user_control_actions
            WHERE dev_number=:d AND result='pending' AND acted_at>now()-interval '30 seconds'
            LIMIT 1"""
            ),
            {"d": dev_number},
        )
        if pending.scalar_one_or_none() is not None:
            raise BizError(ErrCode.BIZ_FAIL, "another command is still pending")
        await control_repo.insert_action(
            session,
            dev_number=dev_number,
            user_name=user.user_name,
            cmd_id=cmd_id,
            action=action,
            usr_group=user.usr_group,
        )
    # The database is authoritative. Worker ignores stream-supplied register/value fields.
    try:
        await xadd_control_cmd(r, cmd_id=cmd_id, payload={"dev_number": dev_number})
    except (RedisError, OSError):
        # XADD may have reached Redis even if its reply was lost. Return the durable
        # command ID so the client follows its result instead of creating another write.
        logger.exception("DO queue publication unconfirmed device=%s cmd=%s", dev_number, cmd_id)
    return ok(data={"cmd_id": cmd_id, "status": "pending"})


@router.get("/{dev_number}/do-commands/{cmd_id}", response_model=ApiResponse)
async def get_do_command(
    dev_number: str,
    cmd_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_ca(user, bit=0x01)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        row = (
            (
                await session.execute(
                    text(  # noqa: TNL001 (transaction-local RLS plus current command owner)
                        """SELECT cmd_id,result,acted_at,completed_at,
            action->'execution' AS execution FROM user_control_actions
            WHERE dev_number=:d AND cmd_id=:c AND user_name=:u"""
                    ),
                    {"d": dev_number, "c": cmd_id, "u": user.user_name},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BizError(ErrCode.BAD_PARAM, "command not found")
        return ok(data=dict(row))
