"""Device template API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import devices as devices_repo
from ..deps import get_current_user, get_session
from .schemas.templates import (
    DeviceTemplateCreateRequest,
    DeviceTemplateOut,
    DeviceTemplateUpdateRequest,
)

router = APIRouter(prefix="/api/device-templates", tags=["device-templates"])


def _template_dump(t: object) -> dict[str, object]:
    return DeviceTemplateOut.model_validate(t).model_dump()


@router.get("", response_model=ApiResponse)
async def list_device_templates(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows = await devices_repo.list_templates(session)
    return ok(data={"items": [_template_dump(t) for t in rows]})


@router.post("", response_model=ApiResponse)
async def create_device_template(
    body: DeviceTemplateCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        t = await devices_repo.create_template(
            session,
            name=body.name,
            dev_type=body.dev_type,
            payload=body.payload.model_dump(exclude_none=True),
        )
    return ok(data=_template_dump(t))


@router.put("/{template_id}", response_model=ApiResponse)
async def update_device_template(
    template_id: int,
    body: DeviceTemplateUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    updates = body.model_dump(exclude_unset=True)
    if "payload" in updates and body.payload is not None:
        updates["payload"] = body.payload.model_dump(exclude_none=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields to update")
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        t = await devices_repo.get_template(session, template_id)
        if t is None:
            raise BizError(ErrCode.BAD_PARAM, "template not found")
        await devices_repo.update_template_fields(session, t, updates)
    return ok(data=_template_dump(t))


@router.delete("/{template_id}", response_model=ApiResponse)
async def delete_device_template(
    template_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        t = await devices_repo.get_template(session, template_id)
        if t is None:
            raise BizError(ErrCode.BAD_PARAM, "template not found")
        await devices_repo.delete_template(session, t)
    return ok(data={"deleted": template_id})
