"""Plans 仓储（timing_plans / maintain_plans）。"""

from __future__ import annotations

from datetime import UTC, datetime

from ruisheng_shared.models.plans import MaintainPlan, TimingPlan
from sqlalchemy import select, text
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


# ---------------------------------------------------------------------------
# Maintenance plans
# ---------------------------------------------------------------------------


async def list_maintain_plans(
    session: AsyncSession, dev_number: str | None = None
) -> list[MaintainPlan]:
    stmt = select(MaintainPlan).where(MaintainPlan.deleted_at.is_(None))
    if dev_number:
        stmt = stmt.where(MaintainPlan.dev_number == dev_number)
    return list((await session.execute(stmt)).scalars())


async def get_maintain_plan(session: AsyncSession, plan_id: int) -> MaintainPlan | None:
    return (
        await session.execute(
            select(MaintainPlan).where(
                MaintainPlan.id == plan_id, MaintainPlan.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()


async def create_maintain_plan(session: AsyncSession, **fields: object) -> MaintainPlan:
    p = MaintainPlan(**fields)
    session.add(p)
    await session.flush()
    return p


async def soft_delete_maintain_plan(session: AsyncSession, plan: MaintainPlan) -> None:
    plan.deleted_at = datetime.now(UTC)
    await session.flush()


async def complete_maintenance(
    session: AsyncSession,
    *,
    plan_id: int,
    action_uuid: str,
    dev_number: str,
    user_name: str,
    note: str | None,
    usr_group: str,
) -> tuple[bool, dict[str, object]]:
    sql = text("""
        WITH locked AS (
            SELECT id, interval_days, next_due_at FROM maintain_plans
             WHERE id = :p AND deleted_at IS NULL AND enable = true
             FOR UPDATE
        ), ins AS (
            INSERT INTO maintain_actions (action_uuid, plan_id, dev_number, user_name, note, usr_group)
            SELECT :u, :p, :d, :n, :note, :g FROM locked
            ON CONFLICT (action_uuid) DO NOTHING
            RETURNING id
        ), upd AS (
            UPDATE maintain_plans p
               SET next_due_at = GREATEST(now(), p.next_due_at)
                                 + make_interval(days => p.interval_days),
                   updated_at = now()
             FROM locked
             WHERE p.id = locked.id AND EXISTS (SELECT 1 FROM ins)
             RETURNING p.id, p.next_due_at
        )
        SELECT
           (SELECT id FROM locked) AS plan_id,
           (SELECT id FROM ins) AS action_id,
           (SELECT next_due_at FROM upd) AS next_due_at
    """)
    res = await session.execute(
        sql,
        {
            "p": plan_id,
            "u": action_uuid,
            "d": dev_number,
            "n": user_name,
            "note": note,
            "g": usr_group,
        },
    )
    row = res.one()
    return bool(row.action_id), {"plan_id": row.plan_id, "next_due_at": row.next_due_at}
