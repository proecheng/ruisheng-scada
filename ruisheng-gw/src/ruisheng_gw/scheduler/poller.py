"""per-device polling coroutine.

Reads `update_interval_decisec` from registry entry; sleeps that
many deciseconds / 10; re-looks-up session writer EACH poll (v2 B7
— handles DTU reconnect where writer may be stale); acquires
per-bus lock before sending; releases on response path (response
handling is in read_loop, NOT here).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ruisheng_gw.protocol.frames import ReadHoldingRequest, encode_read_holding_request
from ruisheng_gw.scheduler.bus_lock import BusLocks, BusLockTimeout
from ruisheng_gw.scheduler.clock import Clock
from ruisheng_gw.transport.session import PendingRead

if TYPE_CHECKING:
    from ruisheng_gw.domain.registry import PointEntry, RegistryEntry


@dataclass(frozen=True)
class PollRead:
    fun_code: int
    start_addr: int
    quantity: int
    points: tuple[PointEntry, ...]


def _build_poll_reads(entry: RegistryEntry) -> list[PollRead]:
    points = sorted(
        entry.points.values(),
        key=lambda p: (p.point.fun_code, p.point.point_number, p.point.point_id),
    )
    groups: list[PollRead] = []
    for point_entry in points:
        point = point_entry.point
        if point.fun_code not in (1, 2, 3, 4):
            continue
        span = point_entry.register_span
        if not groups:
            groups.append(
                PollRead(
                    fun_code=point.fun_code,
                    start_addr=point.point_number,
                    quantity=span,
                    points=(point_entry,),
                )
            )
            continue
        last = groups[-1]
        expected_next = last.start_addr + last.quantity
        if point.fun_code == last.fun_code and point.point_number <= expected_next:
            end_addr = max(expected_next, point.point_number + span)
            groups[-1] = PollRead(
                fun_code=last.fun_code,
                start_addr=last.start_addr,
                quantity=end_addr - last.start_addr,
                points=(*last.points, point_entry),
            )
        else:
            groups.append(
                PollRead(
                    fun_code=point.fun_code,
                    start_addr=point.point_number,
                    quantity=span,
                    points=(point_entry,),
                )
            )
    return groups


def _next_poll_read(entry: RegistryEntry) -> PollRead | None:
    reads = _build_poll_reads(entry)
    if not reads:
        return None
    index = entry.poll_cursor % len(reads)
    entry.poll_cursor = (index + 1) % len(reads)
    return reads[index]


async def poll_once(
    *,
    dev_number: str,
    entry: RegistryEntry,
    session: object,
    bus_locks: BusLocks,
) -> None:
    entry_info = session.get(dev_number)  # type: ignore[attr-defined]
    if entry_info is None or entry_info.writer is None:
        return
    poll_read = _next_poll_read(entry)
    if poll_read is None:
        return
    bus_id = entry_info.bus_id
    req = ReadHoldingRequest(
        slave=entry.modbus_addr,
        start_addr=poll_read.start_addr,
        register_count=poll_read.quantity,
        fun_code=poll_read.fun_code,
    )
    frame = encode_read_holding_request(req)
    try:
        async with bus_locks.acquire(bus_id):
            session.set_pending_read(  # type: ignore[attr-defined]
                dev_number,
                PendingRead(
                    dev_number=dev_number,
                    fun_code=poll_read.fun_code,
                    start_addr=poll_read.start_addr,
                    quantity=poll_read.quantity,
                    points=poll_read.points,
                ),
            )
            entry_info.writer.write(frame)
            await entry_info.writer.drain()
    except BusLockTimeout:
        # metric bus_lock_timeout_total{bus} — recorded by caller
        return


async def poller_loop(
    *,
    dev_number: str,
    entry: RegistryEntry,
    session: object,
    bus_locks: BusLocks,
    clock: Clock,
) -> None:
    interval_sec = entry.update_interval_decisec / 10.0
    while True:
        await clock.sleep(interval_sec)
        await poll_once(
            dev_number=dev_number,
            entry=entry,
            session=session,
            bus_locks=bus_locks,
        )
