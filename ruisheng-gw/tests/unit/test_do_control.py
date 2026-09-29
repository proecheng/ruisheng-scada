from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from ruisheng_gw.control_worker import PROFILE, ControlWorker, reject_reason
from ruisheng_gw.domain.registry import Registry
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame, verify_crc16
from ruisheng_gw.scheduler.serial_poller import SerialPoller
from ruisheng_gw.transport.session import SessionMap


def rig(initial=2, mode="ok"):
    registry = Registry.build(
        device_rows=[
            {
                "dev_number": "DEV001",
                "usr_group": "test",
                "transport_type": "serial",
                "serial_port": "test",
                "modbus_addr": 1,
                "read_profile": "zero_origin_38",
                "update_interval_decisec": 10,
            }
        ],
        point_rows=[
            {
                "id": 1,
                "dev_number": "DEV001",
                "point_number": 1,
                "point_ratio": 1,
                "point_offset": 0,
                "user_ratio": 1,
                "user_point_offset": 0,
            }
        ],
    )
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.is_closing.return_value = False
    writer.drain = AsyncMock()
    ingest = AsyncMock()
    poller = SerialPoller(
        port="test",
        registry=registry,
        session_map=SessionMap(),
        writer=writer,
        on_frame=ingest,
        discard_input=lambda: None,
        response_timeout_sec=0.15,
        quiet_time_sec=0.001,
    )
    state = {"value": initial, "writes": [], "reads": 0, "read_frames": []}

    def write(frame):
        verify_crc16(frame)
        if frame[1] == 3:
            state["reads"] += 1
            state["read_frames"].append(frame.hex())
            count = int.from_bytes(frame[4:6], "big")
            if mode == "no_initial" and state["reads"] == 1:
                return
            value = state["value"]
            if mode == "mismatch" and state["writes"]:
                value ^= 2
            # The field board returns a zero-origin prefix even for a nonzero
            # subread. Keep DI=3 distinct from DO so a false success is visible.
            registers = [3, value] + [100 + n for n in range(36)]
            data = b"".join(v.to_bytes(2, "big") for v in registers[:count])
            reply = append_crc_to_frame(bytes([1, 3, 2 * count]) + data)
            if mode == "short_read":
                reply = append_crc_to_frame(b"\x01\x03\x02\x00\x03")
        else:
            state["writes"].append(frame.hex())
            channel = frame[3]
            high = frame[4:6] == b"\xff\x00"
            assert high or frame[4:6] == b"\x00\xff"
            state["value"] = (
                (state["value"] | (1 << channel)) if high else (state["value"] & ~(1 << channel))
            )
            reply = frame
            if mode == "no_echo" or (mode == "second_fails" and channel == 1):
                return
            if mode == "wrong_echo":
                reply = append_crc_to_frame(frame[:3] + bytes([1 - channel]) + frame[4:6])
            if mode == "bad_crc":
                reply = frame[:-1] + bytes([frame[-1] ^ 1])
            if mode == "exception":
                reply = append_crc_to_frame(b"\x01\x85\x02")
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.create_task(poller.receive_frame(reply))
        )

    writer.write.side_effect = write
    return poller, registry.get("DEV001"), state, ingest


async def execute(poller, entry, mask, value, remaining=12):
    task = asyncio.create_task(poller.do_control.submit(entry, mask, value, remaining))
    await asyncio.sleep(0)
    await poller.do_control.run_one()
    return await task


@pytest.mark.parametrize(
    ("initial", "mask", "value", "expected", "golden"),
    [
        (0, 1, 1, 1, ["01050000ff008c3a"]),
        (1, 1, 0, 0, ["0105000000ff8d8a"]),
        (2, 1, 1, 3, ["01050000ff008c3a"]),
        (3, 1, 0, 2, ["0105000000ff8d8a"]),
        (1, 2, 2, 3, ["01050001ff00ddfa"]),
        (3, 2, 0, 1, ["0105000100ffdc4a"]),
        (0, 3, 3, 3, ["01050000ff008c3a", "01050001ff00ddfa"]),
        (3, 3, 0, 0, ["0105000000ff8d8a", "0105000100ffdc4a"]),
    ],
)
async def test_golden_board_frames_preserve_unselected_channels(
    initial, mask, value, expected, golden
):
    poller, entry, state, ingest = rig(initial)
    result = await execute(poller, entry, mask, value)
    assert result["status"] == "success"
    assert result["before"] == initial and result["readback"] == expected
    assert state["value"] == expected and state["writes"] == golden
    assert set(state["read_frames"]) == {"010300000026c410"}
    assert all(c["phase"] == "verified" for c in result["channels"])
    ingest.assert_not_awaited()  # Control verification does not add unscheduled samples.


@pytest.mark.parametrize(
    "mode",
    ["no_initial", "short_read", "no_echo", "wrong_echo", "bad_crc", "exception", "mismatch"],
)
async def test_unconfirmed_command_stops_without_retry_or_second_write(mode):
    poller, entry, state, _ = rig(0, mode)
    result = await execute(poller, entry, 3, 3)
    assert result["status"] in {"failed", "timeout"}
    assert len(state["writes"]) == (0 if mode in {"no_initial", "short_read"} else 1)


async def test_second_failure_preserves_first_verified_result():
    poller, entry, state, _ = rig(0, "second_fails")
    result = await execute(poller, entry, 3, 3)
    assert result["status"] == "timeout"
    assert [c["phase"] for c in result["channels"]] == ["verified", "sending"]
    assert len(state["writes"]) == 2


@pytest.mark.parametrize("change", ["expired", "cancelled", "removed", "closed"])
async def test_invalidated_queue_cannot_write(change):
    poller, entry, state, _ = rig()
    task = asyncio.create_task(
        poller.do_control.submit(entry, 1, 1, 0 if change == "expired" else 12)
    )
    await asyncio.sleep(0)
    if change == "cancelled":
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    elif change == "removed":
        poller._registry = Registry()
    elif change == "closed":
        poller.do_control.close()
    if not poller.do_control.queue.empty():
        await poller.do_control.run_one()
    await asyncio.gather(task, return_exceptions=True)
    assert state["writes"] == [] and state["reads"] == 0


async def test_real_worker_resumes_regular_poll_after_control():
    poller, entry, state, ingest = rig(0)
    ingested = asyncio.Event()
    ingest.side_effect = lambda *_: ingested.set()
    task = asyncio.create_task(poller.run())
    try:
        result = await poller.do_control.submit(entry, 3, 3)
        assert result["status"] == "success"
        async with asyncio.timeout(1):
            await ingested.wait()
        assert len(state["writes"]) == 2
        assert len(ingest.call_args.args[1]) == 81
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    ("patch", "age", "reason"),
    [
        ({"kind": "old"}, 0, "unsupported_control"),
        ({}, 16, "command_expired"),
        ({}, -1, "command_expired"),
        ({"dispatched_at": "2026-09-21"}, 1, "previous_dispatch"),
        ({"mask": True}, 0, "invalid_channel_selection"),
        ({"mask": 4}, 0, "invalid_channel_selection"),
        ({"value": 2}, 0, "invalid_channel_value"),
        ({"config_version": None}, 0, "missing_configuration"),
    ],
)
def test_old_or_invalid_audit_never_dispatched(patch, age, reason):
    action = {"kind": "do_v1", "profile": PROFILE, "mask": 1, "value": 1, "config_version": 26}
    assert reject_reason(action, 1) is None
    action.update(patch)
    assert reject_reason(action, age).startswith(reason)


async def test_malformed_stream_does_not_reach_database_or_port():
    engine = MagicMock()
    worker = ControlWorker(engine, None, Registry(), [])
    for fields in [
        {},
        {"payload": "[]"},
        {"payload": "bad"},
        {"payload": '{"dev_number":"DEV001"}'},
    ]:
        await worker.handle(fields)
    engine.begin.assert_not_called()
