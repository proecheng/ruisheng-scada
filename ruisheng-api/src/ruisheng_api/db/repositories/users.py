"""users 仓储（Stage C1：load_by_user_name / create_user）。"""

from __future__ import annotations

from ruisheng_shared.models.users import User
from sqlalchemy import select
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
