"""Connection: framer-driven read loop + heartbeat/parse-fail budgeting."""

from __future__ import annotations

import asyncio

from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame
from ruisheng_gw.transport.connection import Connection


async def test_read_loop_emits_frames_to_sink() -> None:
    body = bytes([0x01, 0x03, 0x02, 0x00, 0x0A])  # slave=1 fc=3 bytecount=2 data=10
    frame = append_crc_to_frame(body)
    reader = asyncio.StreamReader()
    reader.feed_data(frame)
    reader.feed_eof()

    sink: list[bytes] = []

    async def on_frame(f: bytes) -> None:
        sink.append(f)

    conn = Connection(reader=reader, writer=None, on_frame=on_frame)  # writer not tested here
    await conn.read_loop()
    assert sink == [frame]


async def test_ten_parse_failures_disconnect() -> None:
    # 10 consecutive garbage chunks that framer can't resolve → disconnect
    garbage = b"\x01\x99\x99\x99\x99\x99\x99\x99\x99\x99" * 20
    reader = asyncio.StreamReader()
    reader.feed_data(garbage)
    reader.feed_eof()

    sink: list[bytes] = []

    async def on_frame(f: bytes) -> None:
        sink.append(f)

    conn = Connection(reader=reader, writer=None, on_frame=on_frame, parse_fail_budget=10)
    # disconnected flag or return early
    await conn.read_loop()
    assert conn.disconnected_for_framing is True
