"""多租户：SET LOCAL app.tenant_id / app.role（对应 spec §3.7 1st layer）。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VALID_ROLES = frozenset({"Administrators", "GroupCompany", "Company", "User"})
TENANT_CONTEXT_MISSING = "query without tenant context"


class TenantIsolationError(RuntimeError):
    """租户上下文缺失或角色非法。"""


async def apply_tenant_context(session: AsyncSession, *, usr_group: str, role: str) -> None:
    if role not in VALID_ROLES:
        raise TenantIsolationError(f"invalid role: {role!r}")
    if role != "Administrators" and not usr_group:
        raise TenantIsolationError(TENANT_CONTEXT_MISSING)
    await session.execute(
        text("SELECT set_config('app.tenant_id', :v, true)"),
        {"v": usr_group or ""},
    )
    await session.execute(
        text("SELECT set_config('app.role', :v, true)"),
        {"v": role},
    )
