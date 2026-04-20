from unittest.mock import AsyncMock, MagicMock

import pytest
from ruisheng_api.core.tenant import (
    TENANT_CONTEXT_MISSING,
    TenantIsolationError,
    apply_tenant_context,
)


@pytest.mark.asyncio
async def test_apply_tenant_context_runs_set_local():
    session = MagicMock()
    session.execute = AsyncMock()
    await apply_tenant_context(session, usr_group="grp1", role="User")
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_apply_tenant_context_admin_may_have_empty_group():
    session = MagicMock()
    session.execute = AsyncMock()
    await apply_tenant_context(session, usr_group="", role="Administrators")
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_apply_tenant_context_non_admin_blank_raises():
    session = MagicMock()
    session.execute = AsyncMock()
    with pytest.raises(TenantIsolationError, match=TENANT_CONTEXT_MISSING):
        await apply_tenant_context(session, usr_group="", role="User")


@pytest.mark.asyncio
async def test_apply_tenant_context_invalid_role():
    session = MagicMock()
    session.execute = AsyncMock()
    with pytest.raises(TenantIsolationError):
        await apply_tenant_context(session, usr_group="g", role="Hacker")
