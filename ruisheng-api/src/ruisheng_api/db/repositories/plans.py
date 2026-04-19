"""Plans 仓储（timing_plans）。"""

from __future__ import annotations

from datetime import UTC, datetime

from ruisheng_shared.models.plans import TimingPlan
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def list_timing_plans(
    session: AsyncSession, dev_number: str | None = None
) -> list[TimingPlan]:
    stmt = select(TimingPlan).where(TimingPlan.deleted_at.is_(None))
    if dev_number:
        stmt = stmt.where(TimingPlan.dev_number == dev_number)
    return list((await session.execute(stmt)).scalars())


async def get_timing_plan(session: AsyncSession, plan_id: int) -> TimingPlan | None:
    return (
        await session.execute(
            select(TimingPlan).where(TimingPlan.id == plan_id, TimingPlan.deleted_at.is_(None))
        )
    ).scalar_one_or_none()


async def create_timing_plan(session: AsyncSession, **fields: object) -> TimingPlan:
    p = TimingPlan(**fields)
    session.add(p)
    await session.flush()
    return p


async def update_timing_plan(
    session: AsyncSession, plan: TimingPlan, updates: dict[str, object]
) -> TimingPlan:
    for k, v in updates.items():
        setattr(plan, k, v)
    # bump update_flag so gw watcher detects the change
    plan.update_flag = (plan.update_flag + 1) % 256
    await session.flush()
    return plan


async def soft_delete_timing_plan(session: AsyncSession, plan: TimingPlan) -> None:
    plan.deleted_at = datetime.now(UTC)
    # bump update_flag so gw watcher detects the deletion
    plan.update_flag = (plan.update_flag + 1) % 256
    await session.flush()
