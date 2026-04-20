from unittest.mock import AsyncMock, MagicMock

import pytest
from ruisheng_api.tasks.pay_expire import expire_stale_pay_orders
from ruisheng_api.tasks.pay_seen_cleanup import cleanup_old_pay_seen


@pytest.mark.asyncio
async def test_expire_stale_calls_update():
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)
    await expire_stale_pay_orders(mock_factory)
    mock_session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_old_pay_seen_calls_delete():
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)
    await cleanup_old_pay_seen(mock_factory)
    mock_session.execute.assert_awaited_once()
