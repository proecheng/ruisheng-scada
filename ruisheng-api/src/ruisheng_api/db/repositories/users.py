"""users 仓储（Stage C1：load_by_user_name / create_user；Stage G1：list/update/soft_delete）。"""

from __future__ import annotations

from datetime import UTC, datetime

from ruisheng_shared.models.users import User
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


async def load_by_user_name(session: AsyncSession, user_name: str) -> User | None:
    stmt = select(User).where(User.user_name == user_name, User.deleted_at.is_(None))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    user_name: str,
    password_hash: str,
    authority: str,
    usr_group: str,
    control_authority: int = 0,
) -> User:
    u = User(
        user_name=user_name,
        password_hash=password_hash,
        authority=authority,
        usr_group=usr_group,
        control_authority=control_authority,
    )
    session.add(u)
    await session.flush()
    return u


async def list_users(
    session: AsyncSession,
    *,
    offset: int,
    limit: int,
    q: str | None = None,
) -> tuple[list[User], int]:
    base = select(User).where(User.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        base = base.where(or_(User.user_name.ilike(like), User.company.ilike(like)))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    stmt = base.order_by(User.id.desc()).offset(offset).limit(limit)
    return list((await session.execute(stmt)).scalars()), int(total)


async def update_user(session: AsyncSession, u: User, updates: dict[str, object]) -> User:
    for k, v in updates.items():
        setattr(u, k, v)
    await session.flush()
    return u


async def soft_delete_user(session: AsyncSession, u: User) -> None:
    u.deleted_at = datetime.now(UTC)
    await session.flush()
