"""per-device polling coroutine.

Reads `update_interval_decisec` from registry entry; sleeps that
many deciseconds / 10; re-looks-up session writer EACH poll (v2 B7
— handles DTU reconnect where writer may be stale); acquires
per-bus lock before sending; releases on response path (response
handling is in read_loop, NOT here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ruisheng_gw.protocol.frames import ReadHoldingRequest, encode_read_holding_request
from ruisheng_gw.scheduler.bus_lock import BusLocks, BusLockTimeout
from ruisheng_gw.scheduler.clock import Clock

if TYPE_CHECKING:
    from ruisheng_gw.domain.registry import RegistryEntry


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
    bus_id = entry_info.bus_id
    # simplest first frame: read holding registers for all points (slave id = 1 default)
    req = ReadHoldingRequest(slave=1, start_addr=0, register_count=max(1, len(entry.points)))
    frame = encode_read_holding_request(req)
    try:
        async with bus_locks.acquire(bus_id):
            await entry_info.writer.write(frame)
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
