"""users 仓储（Stage C1：load_by_user_name / create_user；Stage G1：list/update/soft_delete；Stage G2：wx_groups / phones / emails）。"""

from __future__ import annotations

from datetime import UTC, datetime

from ruisheng_shared.models.tenants import WxGroup
from ruisheng_shared.models.users import User, UserEmail, UserPhoneNumber
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


# ---------------------------------------------------------------------------
# WxGroup
# ---------------------------------------------------------------------------


async def list_wx_groups(session: AsyncSession, *, usr_group: str | None) -> list[WxGroup]:
    """Return all wx_groups (Administrators) or only the tenant's group."""
    stmt = select(WxGroup)
    if usr_group is not None:
        stmt = stmt.where(WxGroup.usr_group == usr_group)
    stmt = stmt.order_by(WxGroup.usr_group)
    result = await session.execute(stmt)
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Phone contacts
# ---------------------------------------------------------------------------


async def list_phones(session: AsyncSession, user_name: str) -> list[UserPhoneNumber]:
    stmt = select(UserPhoneNumber).where(UserPhoneNumber.user_name == user_name)
    result = await session.execute(stmt)
    return list(result.scalars())


async def add_phone(session: AsyncSession, *, user_name: str, phone_number: str) -> UserPhoneNumber:
    p = UserPhoneNumber(user_name=user_name, phone_number=phone_number)
    session.add(p)
    await session.flush()
    return p


async def delete_phone(session: AsyncSession, phone_id: int) -> None:
    stmt = select(UserPhoneNumber).where(UserPhoneNumber.id == phone_id)
    result = await session.execute(stmt)
    p = result.scalar_one_or_none()
    if p is not None:
        await session.delete(p)
        await session.flush()


# ---------------------------------------------------------------------------
# Email contacts
# ---------------------------------------------------------------------------


async def list_emails(session: AsyncSession, user_name: str) -> list[UserEmail]:
    """
    NOTE: UserEmail.phone_number is the FK field that links an email record to a
    phone number (not to a user directly). We join via UserPhoneNumber to resolve
    the owning user.
    """
    stmt = (
        select(UserEmail)
        .join(UserPhoneNumber, UserEmail.phone_number == UserPhoneNumber.phone_number)
        .where(UserPhoneNumber.user_name == user_name)
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def add_email(session: AsyncSession, *, phone_number: str, email: str) -> UserEmail:
    e = UserEmail(phone_number=phone_number, email=email)
    session.add(e)
    await session.flush()
    return e


async def delete_email(session: AsyncSession, email_id: int) -> None:
    stmt = select(UserEmail).where(UserEmail.id == email_id)
    result = await session.execute(stmt)
    e = result.scalar_one_or_none()
    if e is not None:
        await session.delete(e)
        await session.flush()
