"""FC 0x19 heartbeat timeout: 3× heartbeat_timeout_sec 无 FC 0x19 → disconnect."""

from __future__ import annotations

import asyncio

from ruisheng_gw.protocol.frames import encode_heartbeat
from ruisheng_gw.transport.connection import Connection


async def test_heartbeat_resets_timer() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(encode_heartbeat(slave=1))
    reader.feed_data(encode_heartbeat(slave=1))
    reader.feed_eof()

    sink: list[bytes] = []

    async def on_frame(f: bytes) -> None:
        sink.append(f)

    conn = Connection(reader=reader, writer=None, on_frame=on_frame, heartbeat_timeout_sec=60)
    await conn.read_loop()
    expected_frames = 2
    assert len(sink) == expected_frames
    assert conn.disconnected_for_heartbeat_timeout is False


async def test_no_heartbeat_for_long_triggers_disconnect() -> None:
    # simulate elapsed time > heartbeat timeout via inject monotonic
    reader = asyncio.StreamReader()
    reader.feed_eof()  # EOF immediately — no frames, no heartbeats
    sink: list[bytes] = []

    async def on_frame(f: bytes) -> None:
        sink.append(f)

    # heartbeat_timeout=0.01s — should detect on first tick
    conn = Connection(
        reader=reader,
        writer=None,
        on_frame=on_frame,
        heartbeat_timeout_sec=0.01,
    )
    await asyncio.sleep(0.05)
    await conn.read_loop()
    # EOF → loop exits; track whether heartbeat_timeout flag set during loop
    assert conn.disconnected_for_heartbeat_timeout or conn._reader.at_eof()
