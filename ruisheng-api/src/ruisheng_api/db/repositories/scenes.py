"""scenes 仓储（scene_pages + scene_views）。"""

from __future__ import annotations

from datetime import UTC, datetime

from ruisheng_shared.models.scenes import ScenePage, SceneView
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# ScenePage
# ---------------------------------------------------------------------------


async def list_pages(session: AsyncSession) -> list[ScenePage]:
    stmt = select(ScenePage).where(ScenePage.deleted_at.is_(None)).order_by(ScenePage.id.desc())
    return list((await session.execute(stmt)).scalars())


async def get_page(session: AsyncSession, page_id: int) -> ScenePage | None:
    return (
        await session.execute(
            select(ScenePage).where(ScenePage.id == page_id, ScenePage.deleted_at.is_(None))
        )
    ).scalar_one_or_none()


async def create_page(session: AsyncSession, **fields: object) -> ScenePage:
    page = ScenePage(**fields)
    session.add(page)
    await session.flush()
    return page


async def soft_delete_page(session: AsyncSession, page: ScenePage) -> None:
    page.deleted_at = datetime.now(UTC)
    await session.flush()


# ---------------------------------------------------------------------------
# SceneView
# ---------------------------------------------------------------------------


async def list_views(session: AsyncSession, page_id: int) -> list[SceneView]:
    stmt = (
        select(SceneView)
        .where(SceneView.scene_page_id == page_id, SceneView.deleted_at.is_(None))
        .order_by(SceneView.id.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_view(session: AsyncSession, view_id: int) -> SceneView | None:
    return (
        await session.execute(
            select(SceneView).where(SceneView.id == view_id, SceneView.deleted_at.is_(None))
        )
    ).scalar_one_or_none()


async def create_view(session: AsyncSession, **fields: object) -> SceneView:
    view = SceneView(**fields)
    session.add(view)
    await session.flush()
    return view


async def soft_delete_view(session: AsyncSession, view: SceneView) -> None:
    view.deleted_at = datetime.now(UTC)
    await session.flush()
