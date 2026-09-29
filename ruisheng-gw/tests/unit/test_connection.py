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


async def test_serial_discard_drops_completed_read_not_yet_dispatched():
    old_frame = append_crc_to_frame(bytes.fromhex("0103020001"))
    new_frame = append_crc_to_frame(bytes.fromhex("0103020002"))
    reader = asyncio.StreamReader()
    sink: list[bytes] = []

    async def on_frame(frame):
        sink.append(frame)

    conn = Connection(reader=reader, writer=None, on_frame=on_frame, strip_dtu_heartbeats=False)
    task = asyncio.create_task(conn.read_loop())
    try:
        for _ in range(3):
            await asyncio.sleep(0)
        reader.feed_data(old_frame)
        # Complete StreamReader.read, but do not resume Connection's wait_for continuation.
        await asyncio.sleep(0)
        assert conn._read_future is not None and conn._read_future.done()
        conn.discard_input()
        reader.feed_data(new_frame)
        for _ in range(20):
            await asyncio.sleep(0)
        assert sink == [new_frame]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_serial_discard_removes_partial_old_frame_before_new_frame():
    old_frame = append_crc_to_frame(bytes.fromhex("0103020001"))
    new_frame = append_crc_to_frame(bytes.fromhex("0103020002"))
    reader = asyncio.StreamReader()
    sink: list[bytes] = []

    async def on_frame(frame):
        sink.append(frame)

    conn = Connection(reader=reader, writer=None, on_frame=on_frame, strip_dtu_heartbeats=False)
    task = asyncio.create_task(conn.read_loop())
    try:
        reader.feed_data(old_frame[:4])
        for _ in range(20):
            await asyncio.sleep(0)
        assert sink == []
        conn.discard_input()
        reader.feed_data(new_frame)
        for _ in range(20):
            await asyncio.sleep(0)
        assert sink == [new_frame]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
