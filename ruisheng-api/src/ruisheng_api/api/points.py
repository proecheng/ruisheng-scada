"""Points API：嵌套在 /api/devices/{dev_number}/points/*。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import devices as devices_repo
from ..db.repositories import points as points_repo
from ..deps import get_current_user, get_session
from .schemas.points import PointCreateRequest, PointOut, PointUpdateRequest

router = APIRouter(prefix="/api/devices", tags=["points"])


async def _require_dev(session: AsyncSession, dev_number: str, user: CurrentUser) -> object:
    await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
    d = await devices_repo.get_by_dev_number(session, dev_number)
    if d is None:
        raise BizError(ErrCode.BAD_PARAM, "device not found")
    return d


async def _bump_flag(session: AsyncSession, dev_number: str) -> None:
    await session.execute(
        text("UPDATE devices SET update_flag = 1 WHERE dev_number = :d"),
        {"d": dev_number},
    )


@router.get("/{dev_number}/points", response_model=ApiResponse)
async def list_points(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await _require_dev(session, dev_number, user)
        rows = await points_repo.list_points(session, dev_number)
    return ok(data={"items": [PointOut.model_validate(p).model_dump() for p in rows]})


@router.post("/{dev_number}/points", response_model=ApiResponse)
async def create_point(
    dev_number: str,
    body: PointCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    async with session.begin():
        await _require_dev(session, dev_number, user)
        p = await points_repo.create_point(
            session, dev_number=dev_number, **body.model_dump(exclude_none=True)
        )
        await _bump_flag(session, dev_number)
    return ok(data=PointOut.model_validate(p).model_dump())


@router.put("/{dev_number}/points/{point_id}", response_model=ApiResponse)
async def update_point(
    dev_number: str,
    point_id: int,
    body: PointUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields to update")
    async with session.begin():
        await _require_dev(session, dev_number, user)
        p = await points_repo.get_point(session, point_id)
        if p is None or p.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "point not found")
        await points_repo.update_point(session, p, updates)
        await _bump_flag(session, dev_number)
    return ok(data=PointOut.model_validate(p).model_dump())


@router.delete("/{dev_number}/points/{point_id}", response_model=ApiResponse)
async def delete_point(
    dev_number: str,
    point_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await _require_dev(session, dev_number, user)
        p = await points_repo.get_point(session, point_id)
        if p is None or p.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "point not found")
        await points_repo.delete_point(session, p)
        await _bump_flag(session, dev_number)
    return ok(data={"deleted": point_id})
