"""Legacy per-device TCP polling and shared Modbus read-group construction.

Reads `update_interval_decisec` from registry entry; sleeps that
many deciseconds / 10; re-looks-up session writer EACH poll (v2 B7
— handles DTU reconnect where writer may be stale); acquires
per-bus lock before sending. Serial buses use SerialPoller instead,
which owns the complete request/response transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ruisheng_gw.protocol.frames import ReadHoldingRequest, encode_read_holding_request
from ruisheng_gw.scheduler.bus_lock import BusLocks, BusLockTimeout
from ruisheng_gw.scheduler.clock import Clock
from ruisheng_gw.transport.session import PendingRead

if TYPE_CHECKING:
    from ruisheng_gw.domain.registry import PointEntry, Registry, RegistryEntry

_MAX_REGISTER_COUNT = 125
_MAX_BIT_COUNT = 2000
_ADDRESS_SPACE = 65536
_FIXED_REGISTER_COUNT = 38
_BITS_PER_REGISTER = 16
_FC_HOLDING = 3


def _valid_point(point_entry: PointEntry) -> bool:
    point = point_entry.point
    if not 0 <= point.point_number < _ADDRESS_SPACE:
        return False
    if point.fun_code in (1, 2):
        return point.value_type == "bit" and point.r_bit is None
    if point.fun_code not in (3, 4) or point.value_type not in (
        "字",
        "双字",
        "有符号字节",
        "无符号字节",
        "bit",
    ):
        return False
    if point.value_type == "bit" and (
        point.r_bit is None or not 0 <= point.r_bit < _BITS_PER_REGISTER
    ):
        return False
    return point.point_number + point_entry.register_span <= _ADDRESS_SPACE


@dataclass(frozen=True)
class PollRead:
    fun_code: int
    start_addr: int
    quantity: int
    points: tuple[PointEntry, ...]


def _build_poll_reads(entry: RegistryEntry) -> list[PollRead]:
    points = sorted(
        (point for point in entry.points.values() if _valid_point(point)),
        key=lambda p: (p.point.fun_code, p.point.point_number, p.point.point_id),
    )
    if entry.read_profile == "zero_origin_38":
        if entry.transport_type != "serial" or not points:
            return []
        if any(
            p.point.fun_code != _FC_HOLDING
            or p.point.point_number + p.register_span > _FIXED_REGISTER_COUNT
            for p in entry.points.values()
        ):
            return []
        return [PollRead(3, 0, _FIXED_REGISTER_COUNT, tuple(points))]
    if entry.read_profile != "point_groups":
        return []
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
        max_quantity = _MAX_BIT_COUNT if point.fun_code in (1, 2) else _MAX_REGISTER_COUNT
        if (
            point.fun_code == last.fun_code
            and point.point_number <= expected_next
            and max(expected_next, point.point_number + span) - last.start_addr <= max_quantity
        ):
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
    registry: Registry | None = None,
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
            if registry is not None and (
                registry.get(dev_number) is not entry or entry.transport_type != "tcp"
            ):
                return
            current_session = session.get(dev_number)  # type: ignore[attr-defined]
            if current_session is None or (
                current_session.writer is not entry_info.writer
                or current_session.generation != entry_info.generation
                or current_session.bus_id != bus_id
            ):
                return
            closing = getattr(entry_info.writer, "is_closing", None)
            if callable(closing) and closing():
                return
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
            try:
                entry_info.writer.write(frame)
                await entry_info.writer.drain()
            except (ConnectionError, OSError):
                session.set_pending_read(dev_number, None)  # type: ignore[attr-defined]
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
    registry: Registry | None = None,
) -> None:
    interval_sec = entry.update_interval_decisec / 10.0
    while True:
        await clock.sleep(interval_sec)
        if registry is not None and (
            registry.get(dev_number) is not entry or entry.transport_type != "tcp"
        ):
            continue
        await poll_once(
            dev_number=dev_number,
            entry=entry,
            session=session,
            bus_locks=bus_locks,
            registry=registry,
        )
