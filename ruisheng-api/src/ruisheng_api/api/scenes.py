"""Scenes API：/api/scenes/pages (scene_pages + scene_views CRUD)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import scenes as scenes_repo
from ..deps import get_current_user, get_session
from .schemas.scenes import (
    ScenePageCreateRequest,
    ScenePageOut,
    SceneViewCreateRequest,
    SceneViewOut,
)

pages_router = APIRouter(prefix="/api/scenes/pages", tags=["scenes"])
views_router = APIRouter(prefix="/api/scenes/pages", tags=["scenes"])


# ---------------------------------------------------------------------------
# scene_pages
# ---------------------------------------------------------------------------


@pages_router.get("", response_model=ApiResponse)
async def list_pages(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows = await scenes_repo.list_pages(session)
    return ok(data={"items": [ScenePageOut.model_validate(p).model_dump() for p in rows]})


@pages_router.post("", response_model=ApiResponse)
async def create_page(
    body: ScenePageCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    owner = body.owner_user_name or user.user_name
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        fields = body.model_dump(exclude={"owner_user_name"})
        p = await scenes_repo.create_page(
            session,
            **fields,
            owner_user_name=owner,
            usr_group=user.usr_group,
        )
    return ok(data=ScenePageOut.model_validate(p).model_dump())


@pages_router.delete("/{page_id}", response_model=ApiResponse)
async def delete_page(
    page_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        p = await scenes_repo.get_page(session, page_id)
        if p is None:
            raise BizError(ErrCode.BAD_PARAM, "scene page not found")
        await scenes_repo.soft_delete_page(session, p)
    return ok(data={"deleted": page_id})


# ---------------------------------------------------------------------------
# scene_views (nested under pages)
# ---------------------------------------------------------------------------


@views_router.get("/{page_id}/views", response_model=ApiResponse)
async def list_views(
    page_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        p = await scenes_repo.get_page(session, page_id)
        if p is None:
            raise BizError(ErrCode.BAD_PARAM, "scene page not found")
        rows = await scenes_repo.list_views(session, page_id)
    return ok(data={"items": [SceneViewOut.model_validate(v).model_dump() for v in rows]})


@views_router.post("/{page_id}/views", response_model=ApiResponse)
async def create_view(
    page_id: int,
    body: SceneViewCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    owner = body.owner_user_name or user.user_name
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        p = await scenes_repo.get_page(session, page_id)
        if p is None:
            raise BizError(ErrCode.BAD_PARAM, "scene page not found")
        fields = body.model_dump(exclude={"owner_user_name"})
        v = await scenes_repo.create_view(
            session,
            **fields,
            scene_page_id=page_id,
            owner_user_name=owner,
            usr_group=user.usr_group,
        )
    return ok(data=SceneViewOut.model_validate(v).model_dump())


@views_router.delete("/{page_id}/views/{view_id}", response_model=ApiResponse)
async def delete_view(
    page_id: int,
    view_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        v = await scenes_repo.get_view(session, view_id)
        if v is None or v.scene_page_id != page_id:
            raise BizError(ErrCode.BAD_PARAM, "scene view not found")
        await scenes_repo.soft_delete_view(session, v)
    return ok(data={"deleted": view_id})
