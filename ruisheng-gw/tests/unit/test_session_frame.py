from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from ruisheng_gw.main import _ingest_session_frame
from ruisheng_gw.transport.session import PendingRead, SessionMap


@pytest.mark.parametrize("replace_session", [False, True])
async def test_ingestion_only_clears_its_own_pending_read(replace_session):
    sessions = SessionMap()
    sessions.bind_serial(dev_number="D1", writer=MagicMock(), bus_id="COM3")
    original = PendingRead("D1", 3, 0, 38, bus_id="COM3")
    next_pending = PendingRead("D1", 3, 0, 38, bus_id="COM4")
    sessions.set_pending_read("D1", original)

    async def ingest(**kwargs):
        assert kwargs["pending_read"] is original
        if replace_session:
            sessions.bind_serial(dev_number="D1", writer=MagicMock(), bus_id="COM4")
            sessions.set_pending_read("D1", next_pending)

    ingestor = MagicMock()
    ingestor.process_frame_for_pending = AsyncMock(side_effect=ingest)
    await _ingest_session_frame(ingestor, sessions, "D1", b"frame")
    assert sessions.get("D1").pending_read is (next_pending if replace_session else None)
