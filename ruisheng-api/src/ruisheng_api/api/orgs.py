"""Organizations API：/api/orgs/users。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_role
from ..core.response import ApiResponse, ok
from ..core.security import hash_password
from ..core.tenant import apply_tenant_context
from ..db.repositories import users as users_repo
from ..deps import get_current_user, get_session
from .schemas.orgs import UserCreateRequest, UserOut, UserUpdateRequest

router = APIRouter(prefix="/api/orgs", tags=["orgs"])

_ROLE_LEVEL = {"User": 1, "Company": 2, "GroupCompany": 3, "Administrators": 4}


def _must_not_exceed(me: CurrentUser, target_authority: str) -> None:
    if _ROLE_LEVEL.get(target_authority, 99) > _ROLE_LEVEL.get(me.role, 0):
        raise BizError(ErrCode.FORBIDDEN, "cannot grant authority above your own")


@router.get("/users", response_model=ApiResponse)
async def list_users(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    q: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows, total = await users_repo.list_users(session, offset=offset, limit=limit, q=q)
    return ok(
        data={
            "total": total,
            "items": [UserOut.model_validate(u).model_dump() for u in rows],
        }
    )


@router.post("/users", response_model=ApiResponse)
async def create_user(
    body: UserCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    _must_not_exceed(user, body.authority)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        if await users_repo.load_by_user_name(session, body.user_name):
            raise BizError(ErrCode.BAD_PARAM, "user_name exists")
        u = await users_repo.create_user(
            session,
            user_name=body.user_name,
            password_hash=hash_password(body.password),
            authority=body.authority,
            usr_group=user.usr_group,
            control_authority=body.control_authority,
        )
        extra: dict[str, object] = {
            k: v
            for k, v in {
                "group_company": body.group_company,
                "company": body.company,
                "department": body.department,
            }.items()
            if v is not None
        }
        if extra:
            await users_repo.update_user(session, u, extra)
    return ok(data=UserOut.model_validate(u).model_dump())


@router.put("/users/{user_name}", response_model=ApiResponse)
async def update_user(
    user_name: str,
    body: UserUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    if body.authority:
        _must_not_exceed(user, body.authority)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields")
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        u = await users_repo.load_by_user_name(session, user_name)
        if u is None:
            raise BizError(ErrCode.BAD_PARAM, "user not found")
        await users_repo.update_user(session, u, updates)
    return ok(data=UserOut.model_validate(u).model_dump())


@router.delete("/users/{user_name}", response_model=ApiResponse)
async def delete_user(
    user_name: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    if user.user_name == user_name:
        raise BizError(ErrCode.BAD_PARAM, "cannot delete yourself")
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        u = await users_repo.load_by_user_name(session, user_name)
        if u is None:
            raise BizError(ErrCode.BAD_PARAM, "user not found")
        await users_repo.soft_delete_user(session, u)
    return ok(data={"deleted": user_name})
