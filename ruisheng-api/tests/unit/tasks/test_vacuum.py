from unittest.mock import AsyncMock, MagicMock

import pytest
from ruisheng_api.tasks.vacuum_hot import vacuum_hot_tables


@pytest.mark.asyncio
async def test_vacuum_hot_calls_execute():
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=None)
    mock_session.commit = AsyncMock(return_value=None)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)
    await vacuum_hot_tables(mock_factory)
    assert mock_session.execute.await_count >= 1
