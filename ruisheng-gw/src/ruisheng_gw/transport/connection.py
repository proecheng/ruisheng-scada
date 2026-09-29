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
        strip_dtu_heartbeats: bool = True,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._on_frame = on_frame
        self._framer = Framer(strip_dtu_heartbeats=strip_dtu_heartbeats)
        self._read_future: asyncio.Task[bytes] | None = None
        self._input_generation = 0
        self._parse_fail_budget = parse_fail_budget
        self._parse_fail_run = 0
        self._heartbeat_timeout_sec = heartbeat_timeout_sec
        self._last_heartbeat_ts = time.monotonic()
        self.disconnected_for_framing = False
        self.disconnected_for_heartbeat_timeout = False

    def discard_input(self) -> None:
        """Serial-only reset, called synchronously before sending the next request."""
        self._input_generation += 1
        if self._read_future is not None:
            self._read_future.cancel()
        self._framer.clear()
        # StreamReader has no public flush API; cancel its read before clearing queued bytes.
        self._reader._buffer.clear()  # type: ignore[attr-defined]  # noqa: SLF001
        self._parse_fail_run = 0

    async def read_loop(self) -> None:  # noqa: PLR0912
        prev_resync = self._framer.stats["resync"]
        while not self._reader.at_eof():
            now = time.monotonic()
            if now - self._last_heartbeat_ts > self._heartbeat_timeout_sec:
                self.disconnected_for_heartbeat_timeout = True
                return
            try:
                input_generation = self._input_generation
                self._read_future = asyncio.create_task(self._reader.read(_READ_CHUNK))
                data = await asyncio.wait_for(
                    self._read_future,
                    timeout=_IDLE_POLL_MS / 1000,
                )
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                continue
            except TimeoutError:
                self._framer.tick(int(now * 1000))
                continue
            finally:
                self._read_future = None
            if input_generation != self._input_generation:
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
