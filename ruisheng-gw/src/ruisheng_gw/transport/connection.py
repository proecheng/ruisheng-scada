"""Per-connection read loop driven by Framer.

- Read raw bytes via asyncio.StreamReader
- Feed into protocol.framer.Framer (already strips DTU heartbeats)
- Emit complete frames to on_frame callback
- Track parse_fail_budget: accumulated framer resync-byte advances with no emitted
  frame; ≥parse_fail_budget resync advances → disconnected_for_framing=True
- Track heartbeat_timeout_sec: no FC 0x19 within timeout → disconnected_for_heartbeat_timeout=True
- Forward frame bytes to on_frame; session & poller wiring in C4
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from ruisheng_gw.protocol.framer import Framer
from ruisheng_gw.protocol.frames import HeartbeatFrame, decode_frame_by_funcode

_READ_CHUNK = 4096
_IDLE_POLL_MS = 100


class Connection:
    def __init__(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter | None,
        on_frame: Callable[[bytes], Awaitable[None]],
        parse_fail_budget: int = 10,
        heartbeat_timeout_sec: float = 90.0,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._on_frame = on_frame
        self._framer = Framer()
        self._parse_fail_budget = parse_fail_budget
        self._parse_fail_run = 0
        self._heartbeat_timeout_sec = heartbeat_timeout_sec
        self._last_heartbeat_ts = time.monotonic()
        self.disconnected_for_framing = False
        self.disconnected_for_heartbeat_timeout = False

    async def read_loop(self) -> None:
        prev_resync = self._framer.stats["resync"]
        while not self._reader.at_eof():
            now = time.monotonic()
            if now - self._last_heartbeat_ts > self._heartbeat_timeout_sec:
                self.disconnected_for_heartbeat_timeout = True
                return
            try:
                data = await asyncio.wait_for(
                    self._reader.read(_READ_CHUNK),
                    timeout=_IDLE_POLL_MS / 1000,
                )
            except TimeoutError:
                self._framer.tick(int(now * 1000))
                continue
            if not data:
                break
            self._framer.feed(data, now_ms=int(now * 1000))
            emitted_this_round = False
            for frame in self._framer.pop_frames():
                emitted_this_round = True
                try:
                    obj = decode_frame_by_funcode(frame)
                    if isinstance(obj, HeartbeatFrame):
                        self._last_heartbeat_ts = time.monotonic()
                except Exception:  # noqa: BLE001
                    pass
                await self._on_frame(frame)
            new_resync = self._framer.stats["resync"]
            resync_delta = new_resync - prev_resync
            prev_resync = new_resync
            if emitted_this_round:
                self._parse_fail_run = 0  # good frame: clear the run
            elif resync_delta > 0:
                # framer advanced past unrecognised bytes: accumulate failure count
                self._parse_fail_run += resync_delta
                if self._parse_fail_run >= self._parse_fail_budget:
                    self.disconnected_for_framing = True
                    return
            # else: buffer incomplete (waiting for more bytes) — no penalty, no reset
