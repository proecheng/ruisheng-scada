"""Plans API：/api/plans/timing (timing_plans CRUD)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import plans as plans_repo
from ..deps import get_current_user, get_session
from .schemas.plans import TimingPlanCreateRequest, TimingPlanOut, TimingPlanUpdateRequest

timing_router = APIRouter(prefix="/api/plans/timing", tags=["plans"])
maintenance_router = APIRouter(prefix="/api/plans/maintenance", tags=["plans"])


@timing_router.get("", response_model=ApiResponse)
async def list_timing_plans(
    dev_number: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows = await plans_repo.list_timing_plans(session, dev_number=dev_number)
    return ok(data={"items": [TimingPlanOut.model_validate(p).model_dump() for p in rows]})


@timing_router.post("", response_model=ApiResponse)
async def create_timing_plan(
    body: TimingPlanCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        p = await plans_repo.create_timing_plan(
            session, **body.model_dump(exclude_none=True), usr_group=user.usr_group
        )
    return ok(data=TimingPlanOut.model_validate(p).model_dump())


@timing_router.put("/{plan_id}", response_model=ApiResponse)
async def update_timing_plan(
    plan_id: int,
    body: TimingPlanUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields")
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        p = await plans_repo.get_timing_plan(session, plan_id)
        if p is None:
            raise BizError(ErrCode.BAD_PARAM, "plan not found")
        await plans_repo.update_timing_plan(session, p, updates)
    return ok(data=TimingPlanOut.model_validate(p).model_dump())


@timing_router.delete("/{plan_id}", response_model=ApiResponse)
async def delete_timing_plan(
    plan_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        p = await plans_repo.get_timing_plan(session, plan_id)
        if p is None:
            raise BizError(ErrCode.BAD_PARAM, "plan not found")
        await plans_repo.soft_delete_timing_plan(session, p)
    return ok(data={"deleted": plan_id})
