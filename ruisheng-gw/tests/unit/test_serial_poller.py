from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from ruisheng_gw.domain.registry import Registry
from ruisheng_gw.protocol.frames import ExceptionResponse, encode_exception_response
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame
from ruisheng_gw.scheduler.clock import FakeClock
from ruisheng_gw.scheduler.serial_poller import SerialPoller
from ruisheng_gw.transport.session import SessionMap


def make_registry(addresses=(1, 2, 3, 4, 5), *, port="COM3", version=1, interval=10):
    return Registry.build(
        device_rows=[
            {
                "dev_number": f"D{addr:03}",
                "usr_group": "tenant",
                "update_interval_decisec": interval,
                "transport_type": "serial",
                "serial_port": port,
                "modbus_addr": addr,
                "read_profile": "zero_origin_38",
                "update_flag": version,
            }
            for addr in addresses
        ],
        point_rows=[
            {
                "id": addr,
                "dev_number": f"D{addr:03}",
                "point_number": 35,
                "point_ratio": 1.0,
                "point_offset": 0.0,
                "user_ratio": 1.0,
                "user_point_offset": 0.0,
            }
            for addr in addresses
        ],
    )


def response(addr=1, *, count=38, fc=3, value=17):
    return append_crc_to_frame(bytes([addr, fc, count * 2]) + value.to_bytes(2, "big") * count)


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


def make_poller(registry, *, port="COM3", session=None, clock=None, retries=1):
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.is_closing.return_value = False
    writer.drain = AsyncMock()
    callback = AsyncMock()
    clock = clock or FakeClock()
    session = session if session is not None else SessionMap()
    poller = SerialPoller(
        port=port,
        registry=registry,
        session_map=session,
        writer=writer,
        on_frame=callback,
        discard_input=MagicMock(),
        clock=clock,
        read_timeout_retries=retries,
    )
    return poller, writer, callback, clock, session


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_board_power_loss_keeps_polling_past_offline_threshold_then_recovers():
    """A silent board remains in the poll roster; no re-enable/restart is needed."""
    registry = make_registry((1,), interval=50)
    poller, writer, callback, clock, _ = make_poller(registry)
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        for round_index in range(4):
            assert writer.write.call_count == round_index * 2 + 1
            clock.advance(1)
            await settle()
            clock.advance(0.201)
            await settle()
            assert writer.write.call_count == round_index * 2 + 2
            clock.advance(1)
            await settle()
            clock.advance(0.201)
            await settle()
            clock.advance(2.601)
            await settle()
        assert registry.get("D001").loss_count == 8
        assert writer.write.call_count == 9
        assert not task.done()
        await poller.receive_frame(response(1))
        await settle()
        callback.assert_awaited_once()
        assert registry.get("D001").loss_count == 0
        assert registry.get("D001").last_back > 0
        assert all(call.args[0].hex() == "010300000026c410" for call in writer.write.call_args_list)
    finally:
        await stop(task)


async def test_five_devices_two_fair_rounds_have_one_pending_and_quiet_time():
    poller, writer, callback, clock, session = make_poller(make_registry())
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        expected = [
            "010300000026c410",
            "020300000026c423",
            "030300000026c5f2",
            "040300000026c445",
            "050300000026c594",
        ] * 2
        for index, golden in enumerate(expected):
            assert writer.write.call_count == index + 1
            assert writer.write.call_args.args[0].hex() == golden
            assert (
                sum(session.get(f"D{addr:03}").pending_read is not None for addr in range(1, 6))
                == 1
            )
            await settle()
            assert writer.write.call_count == index + 1
            await poller.receive_frame(response(index % 5 + 1))
            await settle()
            assert callback.await_count == index + 1
            clock.advance(0.199)
            await settle()
            assert writer.write.call_count == index + 1
            clock.advance(0.002)
            await settle()
    finally:
        await stop(task)
    assert len(session) == 0


async def test_timeout_has_no_retry_and_late_other_slave_does_not_complete_next():
    poller, writer, callback, clock, _ = make_poller(make_registry((1, 7)))
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        clock.advance(1)
        await poller.receive_frame(response(1))  # deadline reached before timeout task resumes
        await settle()
        assert callback.await_count == 0
        assert poller.diagnostics["timeouts"] == 1
        assert poller._registry.get("D001").loss_count == 1
        assert poller._registry.get("D001").last_call > 0
        assert poller._registry.get("D001").last_back == 0
        clock.advance(0.2)
        await settle()
        assert writer.write.call_count == 2
        assert writer.write.call_args.args[0][0] == 7
        await poller.receive_frame(response(1))
        await settle()
        assert callback.await_count == 0
        await poller.receive_frame(response(7))
        await settle()
        assert callback.await_args.args[0] == "D007"
    finally:
        await stop(task)


@pytest.mark.parametrize(
    "bad",
    [
        response(99),
        response(1, count=1),
        response(1, fc=4),
        response(1)[:-1] + b"\x00",
        encode_exception_response(ExceptionResponse(1, 4, 2)),
    ],
)
async def test_unmatched_and_invalid_frames_do_not_release_current_transaction(bad):
    poller, writer, callback, clock, session = make_poller(make_registry((1, 2)))
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        await poller.receive_frame(bad)
        clock.advance(0.5)
        await settle()
        assert writer.write.call_count == 1
        assert session.get("D001").pending_read is not None
        callback.assert_not_awaited()
        assert poller._registry.get("D001").last_back == 0
        await poller.receive_frame(response(1))
        await settle()
        callback.assert_awaited_once()
        assert poller._registry.get("D001").last_back > 0
        assert poller._registry.get("D001").loss_count == 0
    finally:
        await stop(task)


async def test_valid_exception_finishes_without_measurements_and_others_continue():
    poller, writer, callback, clock, _ = make_poller(make_registry((1, 2)))
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        await poller.receive_frame(encode_exception_response(ExceptionResponse(1, 3, 2)))
        await settle()
        callback.assert_not_awaited()
        assert poller.diagnostics["exceptions"] == 1
        assert poller._registry.get("D001").loss_count == 1
        assert poller._registry.get("D001").last_back == 0
        clock.advance(0.2)
        await settle()
        assert writer.write.call_args.args[0][0] == 2
    finally:
        await stop(task)


async def test_empty_start_hot_add_double_change_and_soft_deleted_address_reuse():
    registry = make_registry(())
    poller, writer, callback, clock, session = make_poller(registry)
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        assert writer.write.call_count == 0
        registry.reconcile_serial(make_registry((1,)))
        clock.advance(0.05)
        await settle()
        old_pending = session.get("D001").pending_read
        assert old_pending is not None
        registry.reconcile_serial(make_registry((1,), version=2))
        registry.reconcile_serial(make_registry((1,), version=3))
        await poller.receive_frame(response(1))
        callback.assert_not_awaited()
        registry.reconcile_serial(make_registry(()))
        replacement = make_registry((1,), version=1)
        entry = replacement._entries.pop("D001")
        entry.device = replace(entry.device, dev_number="NEW")
        entry.points = {
            101: replace(
                entry.points[1],
                point=replace(entry.points[1].point, point_id=101, dev_number="NEW"),
            )
        }
        replacement._entries["NEW"] = entry
        registry.reconcile_serial(replacement)
        assert session.get("NEW") is None
        clock.advance(1)
        await settle()
        clock.advance(0.2)
        await settle()
        assert session.get("D001") is None
        assert session.get("NEW").pending_read.registry_entry is entry
        await poller.receive_frame(response(1))
        await settle()
        assert callback.await_args.args[0] == "NEW"
    finally:
        await stop(task)


async def test_two_ports_independent_and_old_port_cleanup_preserves_new_binding():
    registry = make_registry((1,))
    session = SessionMap()
    first, writer1, callback1, clock1, _ = make_poller(registry, session=session)
    task1 = asyncio.create_task(first.run())
    task2 = None
    try:
        await settle()
        registry.reconcile_serial(make_registry((1,), port="COM4", version=2))
        second, writer2, callback2, _, _ = make_poller(registry, port="COM4", session=session)
        task2 = asyncio.create_task(second.run())
        await settle()
        assert writer1.write.call_count == writer2.write.call_count == 1
        assert session.get("D001").writer is writer2
        await stop(task1)
        assert session.get("D001").writer is writer2
        await first.receive_frame(response(1))
        callback1.assert_not_awaited()
        await second.receive_frame(response(1))
        await settle()
        callback2.assert_awaited_once()
    finally:
        await stop(task1)
        if task2 is not None:
            await stop(task2)


async def test_no_valid_points_produces_zero_transmissions():
    registry = make_registry((1,))
    registry.get("D001").points.clear()
    poller, writer, _, clock, _ = make_poller(registry)
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        clock.advance(10)
        await settle()
        writer.write.assert_not_called()
    finally:
        await stop(task)


async def test_send_timeout_fails_worker_without_sending_to_next_device():
    poller, writer, callback, clock, session = make_poller(make_registry((1, 2)))
    never = asyncio.Event()
    writer.drain.side_effect = never.wait
    task = asyncio.create_task(poller.run())
    await settle()
    clock.advance(1)
    await settle()
    with pytest.raises(TimeoutError, match="send did not complete"):
        await task
    assert writer.write.call_count == 1
    assert len(session) == 0
    callback.assert_not_awaited()


async def test_slow_ingestion_does_not_block_receive_callback_or_start_next_transaction():
    poller, writer, callback, clock, _ = make_poller(make_registry((1, 2)))
    release = asyncio.Event()
    callback.side_effect = lambda *args: None

    async def ingest(*args):
        await release.wait()

    callback.side_effect = ingest
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        await poller.receive_frame(response(1))
        await settle()
        await poller.receive_frame(response(2))
        clock.advance(2)
        await settle()
        assert writer.write.call_count == 1
        release.set()
        await settle()
        clock.advance(0.2)
        await settle()
        assert writer.write.call_count == 2
    finally:
        await stop(task)


async def test_stuck_ingestion_is_cancelled_and_next_device_continues():
    poller, writer, callback, clock, session = make_poller(make_registry((1, 2)))
    poller._response_timeout_sec = 0.01
    cancelled = asyncio.Event()

    async def stuck(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    callback.side_effect = stuck
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        await poller.receive_frame(response(1))
        await asyncio.wait_for(cancelled.wait(), timeout=5)
        await settle()
        assert poller.diagnostics["ingestion_timeouts"] == 1
        assert session.get("D001").pending_read is None
        assert writer.write.call_count == 1
        clock.advance(0.2)
        await settle()
        assert writer.write.call_args.args[0][0] == 2
    finally:
        await stop(task)


async def test_retry_recovers_once_preserves_request_pending_and_original_cadence(caplog):
    registry = make_registry((1,), interval=50)
    # Two disjoint groups: a retry must not advance the point-group cursor.
    entry = registry.get("D001")
    entry.read_profile = "point_groups"
    original = entry.points[1]
    entry.points[2] = replace(original, point=replace(original.point, point_id=2, point_number=99))
    poller, writer, callback, clock, session = make_poller(registry)
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        pending = session.get("D001").pending_read
        assert entry.poll_cursor == 1
        clock.advance(1)
        await settle()
        assert session.get("D001").pending_read is None
        await poller.receive_frame(response(count=1))  # late frame during quiet time
        callback.assert_not_awaited()
        clock.advance(0.199)
        await settle()
        assert writer.write.call_count == 1
        clock.advance(0.0011)
        await settle()
        assert writer.write.call_count == 2
        assert writer.write.call_args_list[0] == writer.write.call_args_list[1]
        assert session.get("D001").pending_read is pending
        assert entry.poll_cursor == 1
        assert poller._discard_input.call_count == 2
        await poller.receive_frame(response(count=1))
        await poller.receive_frame(response(count=1))
        await settle()
        callback.assert_awaited_once()
        assert session.get("D001").pending_read is None
        assert entry.loss_count == 0
        assert poller.diagnostics["timeouts"] == 1
        assert poller.diagnostics["retry_attempts"] == 1
        assert poller.diagnostics["retry_recovered"] == 1
        assert poller.diagnostics["retry_exhausted"] == 0
        assert "serial response timeout" in caplog.text
        assert "serial retry recovered" in caplog.text
        clock.advance(3.79)
        await settle()
        assert writer.write.call_count == 2
        clock.advance(0.06)
        await settle()
        assert writer.write.call_count == 3
        assert writer.write.call_args.args[0][2:4] == b"\x00\x63"
    finally:
        await stop(task)


async def test_retry_exhaustion_is_bounded_and_next_round_waits_for_original_period():
    poller, writer, callback, clock, session = make_poller(make_registry((1,), interval=50))
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        clock.advance(1)
        await settle()
        clock.advance(0.2)
        await settle()
        clock.advance(1)
        await settle()
        assert writer.write.call_count == 2
        assert poller.diagnostics["timeouts"] == 2
        assert poller.diagnostics["retry_exhausted"] == 1
        assert poller.diagnostics["retry_recovered"] == 0
        assert session.get("D001").pending_read is None
        assert poller._active is None
        await poller.receive_frame(response())
        callback.assert_not_awaited()
        clock.advance(2.79)
        await settle()
        assert writer.write.call_count == 2
        clock.advance(0.06)
        await settle()
        assert writer.write.call_count == 3
    finally:
        await stop(task)


@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "short_period",
        "another_device",
        "changed",
        "deleted",
        "closed",
        "budget_elapsed",
    ],
)
async def test_retry_guards_are_rechecked_after_timeout(change):
    registry = make_registry((1,), interval=50)
    poller, writer, callback, clock, _ = make_poller(
        registry, retries=0 if change == "disabled" else 1
    )
    if change == "short_period":
        registry.get("D001").update_interval_decisec = 30
    poller.reconcile_sessions()
    task = asyncio.create_task(poller._transact(registry.get("D001")))
    try:
        await settle()
        clock.advance(1)
        await settle()
        if change == "another_device":
            registry.reconcile_serial(make_registry((1, 2), interval=50))
        elif change == "changed":
            registry.reconcile_serial(make_registry((1,), interval=50, version=2))
        elif change == "deleted":
            registry.reconcile_serial(make_registry(()))
        elif change == "closed":
            writer.is_closing.return_value = True
        clock.advance(4 if change == "budget_elapsed" else 0.2)
        await settle()
        assert task.done()
        await task
        assert writer.write.call_count == 1
        assert poller.diagnostics["retry_attempts"] == 0
        callback.assert_not_awaited()
    finally:
        await stop(task)


@pytest.mark.parametrize("during_retry", [False, True])
async def test_cancel_during_retry_quiet_or_response_clears_pending(during_retry):
    poller, writer, callback, clock, session = make_poller(make_registry((1,), interval=50))
    task = asyncio.create_task(poller.run())
    await settle()
    clock.advance(1)
    await settle()
    if during_retry:
        clock.advance(0.2)
        await settle()
        assert session.get("D001").pending_read is not None
    await stop(task)
    assert len(session) == 0
    assert poller._active is None
    assert writer.write.call_count == (2 if during_retry else 1)
    callback.assert_not_awaited()


@pytest.mark.parametrize("failure", ["exception", "ingestion", "send"])
async def test_non_response_failures_never_trigger_retry(failure):
    poller, writer, callback, clock, _ = make_poller(make_registry((1,), interval=50))
    poller.reconcile_sessions()
    if failure == "send":
        writer.drain.side_effect = asyncio.Event().wait
    elif failure == "ingestion":
        callback.side_effect = RuntimeError("ingestion unavailable")
    task = asyncio.create_task(poller._transact(poller._registry.get("D001")))
    await settle()
    if failure == "send":
        clock.advance(1)
        await settle()
        with pytest.raises(TimeoutError, match="send did not complete"):
            await task
    else:
        frame = (
            encode_exception_response(ExceptionResponse(1, 3, 2))
            if failure == "exception"
            else response()
        )
        await poller.receive_frame(frame)
        await settle()
        await task
    assert writer.write.call_count == 1
    assert poller.diagnostics["retry_attempts"] == 0


@pytest.mark.parametrize("value", [-1, 2, True, 1.0])
def test_retry_limit_rejects_unbounded_or_coerced_values(value):
    with pytest.raises(ValueError, match="read_timeout_retries"):
        make_poller(make_registry(), retries=value)


async def test_multidevice_bus_does_not_retry_even_with_long_period():
    poller, writer, _, clock, _ = make_poller(make_registry((1, 2), interval=50))
    task = asyncio.create_task(poller.run())
    try:
        await settle()
        clock.advance(1)
        await settle()
        clock.advance(0.2)
        await settle()
        assert [call.args[0][0] for call in writer.write.call_args_list] == [1, 2]
        assert poller.diagnostics["retry_attempts"] == 0
    finally:
        await stop(task)


@pytest.mark.parametrize("failure", ["exception", "ingestion"])
async def test_retry_response_failure_is_not_reported_as_recovered(failure):
    poller, writer, callback, clock, _ = make_poller(make_registry((1,), interval=50))
    poller.reconcile_sessions()
    task = asyncio.create_task(poller._transact(poller._registry.get("D001")))
    await settle()
    clock.advance(1)
    await settle()
    clock.advance(0.2)
    await settle()
    if failure == "ingestion":
        callback.side_effect = RuntimeError("ingestion unavailable")
    frame = (
        encode_exception_response(ExceptionResponse(1, 3, 2))
        if failure == "exception"
        else response()
    )
    await poller.receive_frame(frame)
    await settle()
    await task
    assert writer.write.call_count == 2
    assert poller.diagnostics["retry_recovered"] == 0
