"""One complete, bounded Modbus RTU transaction at a time per physical serial bus."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ruisheng_gw.domain.registry import Registry, RegistryEntry
from ruisheng_gw.protocol.exceptions import ProtocolError
from ruisheng_gw.protocol.frames import (
    ExceptionResponse,
    ReadHoldingRequest,
    ReadHoldingResponse,
    decode_frame_by_funcode,
    encode_read_holding_request,
)
from ruisheng_gw.scheduler.clock import Clock, RealClock
from ruisheng_gw.scheduler.do_control import DoController
from ruisheng_gw.scheduler.poller import _build_poll_reads, _next_poll_read
from ruisheng_gw.transport.session import PendingRead, SessionMap

logger = logging.getLogger(__name__)
_ROSTER_CHECK_SEC = 0.05
_EXCEPTION_FRAME_LENGTH = 5


@dataclass(frozen=True)
class _Transaction:
    entry: RegistryEntry
    pending: PendingRead
    response: asyncio.Future[bytes]
    deadline: float


class SerialPoller:
    def __init__(
        self,
        *,
        port: str,
        registry: Registry,
        session_map: SessionMap,
        writer: asyncio.StreamWriter,
        on_frame: Callable[[str, bytes], Awaitable[None]],
        discard_input: Callable[[], None],
        clock: Clock | None = None,
        response_timeout_sec: float = 1.0,
        quiet_time_sec: float = 0.2,
        read_timeout_retries: int = 1,
    ) -> None:
        if response_timeout_sec <= 0 or quiet_time_sec < 0:
            raise ValueError("invalid serial transaction timing")
        if type(read_timeout_retries) is not int or read_timeout_retries not in (0, 1):
            raise ValueError("read_timeout_retries must be 0 or 1")
        self._port = port
        self._registry = registry
        self._session_map = session_map
        self._writer = writer
        self._on_frame = on_frame
        self._discard_input = discard_input
        self._clock = clock or RealClock()
        self._response_timeout_sec = response_timeout_sec
        self._quiet_time_sec = quiet_time_sec
        self._read_timeout_retries = read_timeout_retries
        self._bound: dict[str, RegistryEntry] = {}
        self._last_started: dict[str, float] = {}
        self._last_device: str | None = None
        self._active: _Transaction | None = None
        self.do_control = DoController(self)
        self.diagnostics: dict[str, int] = {
            "timeouts": 0,
            "exceptions": 0,
            "unmatched": 0,
            "invalid": 0,
            "stale": 0,
            "ingestion_timeouts": 0,
            "retry_attempts": 0,
            "retry_recovered": 0,
            "retry_exhausted": 0,
        }

    def reconcile_sessions(self) -> None:
        """Only called at transaction boundaries, never while awaiting a response."""
        if self._active is not None:
            raise RuntimeError("cannot rebind an active serial transaction")
        incoming = {
            entry.device.dev_number: entry
            for entry in self._registry.devices_for_serial_port(self._port)
        }
        for dev_number, previous in self._bound.items():
            if incoming.get(dev_number) is not previous:
                self._session_map.remove_if_writer(dev_number, self._writer)
        for dev_number, entry in incoming.items():
            session = self._session_map.get(dev_number)
            if (
                self._bound.get(dev_number) is not entry
                or session is None
                or session.writer is not self._writer
            ):
                self._session_map.bind_serial(
                    dev_number=dev_number, writer=self._writer, bus_id=self._port
                )
        self._bound = incoming
        self._last_started = {
            dev: started for dev, started in self._last_started.items() if dev in incoming
        }

    async def receive_frame(self, frame: bytes) -> None:  # noqa: PLR0911
        if self.do_control.receive(frame):
            return
        active = self._active
        if active is None or active.response.done():
            self.diagnostics["unmatched"] += 1
            return
        if self._clock.monotonic() >= active.deadline:
            self.diagnostics["stale"] += 1
            return
        if self._registry.get(active.pending.dev_number) is not active.entry:
            self.diagnostics["stale"] += 1
            return
        try:
            decoded = decode_frame_by_funcode(frame)
        except ProtocolError:
            self.diagnostics["invalid"] += 1
            return
        if decoded.slave != active.entry.modbus_addr:
            self.diagnostics["unmatched"] += 1
            return
        if isinstance(decoded, ExceptionResponse):
            valid = (
                decoded.original_fc == active.pending.fun_code
                and len(frame) == _EXCEPTION_FRAME_LENGTH
            )
        else:
            valid = (
                isinstance(decoded, ReadHoldingResponse)
                and decoded.fun_code == active.pending.fun_code
                and decoded.byte_count == active.pending.expected_byte_count
            )
        if not valid:
            self.diagnostics["unmatched"] += 1
            return
        active.response.set_result(frame)

    def _next_due(self) -> RegistryEntry | None:
        entries = sorted(self._bound.values(), key=lambda entry: entry.device.dev_number)
        if self._last_device is not None:
            after = [entry for entry in entries if entry.device.dev_number > self._last_device]
            before = [entry for entry in entries if entry.device.dev_number <= self._last_device]
            entries = after + before
        now = self._clock.monotonic()
        for entry in entries:
            last = self._last_started.get(entry.device.dev_number)
            if last is not None and now < last + entry.update_interval_decisec / 10.0:
                continue
            if _build_poll_reads(entry):
                return entry
        return None

    async def run(self) -> None:
        control_ran = False
        try:
            while True:
                if self._writer.is_closing():
                    raise ConnectionError(f"serial writer closed: {self._port}")
                self.reconcile_sessions()
                if not control_ran and not self.do_control.queue.empty():
                    await self.do_control.run_one()
                    control_ran = True
                    continue
                entry = self._next_due()
                if entry is None:
                    await self._clock.sleep(_ROSTER_CHECK_SEC)
                    continue
                await self._transact(entry)
                control_ran = False
                await self._clock.sleep(self._quiet_time_sec)
        finally:
            self.release_sessions()

    def release_sessions(self) -> None:
        self.do_control.close()
        for dev_number in self._bound:
            self._session_map.remove_if_writer(dev_number, self._writer)
        self._bound.clear()

    def _can_retry(self, entry: RegistryEntry, *, before_quiet: bool) -> bool:
        # Limit recovery to a single-device bus; adding another device immediately
        # restores the existing round-robin policy without delaying its first poll.
        entries = self._registry.devices_for_serial_port(self._port)
        if (
            not self._read_timeout_retries
            or len(entries) != 1
            or entries[0] is not entry
            or self._writer.is_closing()
        ):
            return False
        next_due = (
            self._last_started[entry.device.dev_number] + entry.update_interval_decisec / 10.0
        )
        # Reserve a full response, bounded ingestion and the final quiet interval.
        needed = 2 * self._response_timeout_sec + self._quiet_time_sec
        if before_quiet:
            needed += self._quiet_time_sec
        return self._clock.monotonic() + needed <= next_due

    async def _transact(self, entry: RegistryEntry) -> None:
        poll_read = _next_poll_read(entry)
        if poll_read is None or self._registry.get(entry.device.dev_number) is not entry:
            return
        dev_number = entry.device.dev_number
        pending = PendingRead(
            dev_number=dev_number,
            fun_code=poll_read.fun_code,
            start_addr=poll_read.start_addr,
            quantity=poll_read.quantity,
            points=poll_read.points,
            registry_entry=entry,
            bus_id=self._port,
        )
        self._last_device = dev_number
        self._last_started[dev_number] = self._clock.monotonic()
        timed_out = await self._attempt(entry, pending)
        if not timed_out or not self._can_retry(entry, before_quiet=True):
            return
        await self._clock.sleep(self._quiet_time_sec)
        # The roster, writer or time budget may change during the quiet interval.
        if not self._can_retry(entry, before_quiet=False):
            return
        self.diagnostics["retry_attempts"] += 1
        logger.warning("serial retry attempt port=%s device=%s", self._port, dev_number)
        if await self._attempt(entry, pending, retry=True):
            self.diagnostics["retry_exhausted"] += 1
            logger.warning("serial retry exhausted port=%s device=%s", self._port, dev_number)

    async def _attempt(
        self, entry: RegistryEntry, pending: PendingRead, *, retry: bool = False
    ) -> bool:
        """Return True only for response timeout; never retry sending or ingestion failures.

        RTU has no transaction ID. A late original reply during the retry can be
        accepted for this identical read, but only one reply is ingested per round.
        """
        self._discard_input()
        dev_number = pending.dev_number
        response: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        deadline = self._clock.monotonic() + self._response_timeout_sec
        self._active = _Transaction(entry, pending, response, deadline)
        self._session_map.set_pending_read(dev_number, pending)
        timeout = asyncio.create_task(self._sleep_until(deadline))
        drain: asyncio.Task[None] | None = None
        try:
            self._writer.write(
                encode_read_holding_request(
                    ReadHoldingRequest(
                        slave=entry.modbus_addr,
                        fun_code=pending.fun_code,
                        start_addr=pending.start_addr,
                        register_count=pending.quantity,
                    )
                )
            )
            entry.last_call = time.time()
            drain = asyncio.create_task(self._writer.drain())
            await asyncio.wait((drain, timeout), return_when=asyncio.FIRST_COMPLETED)
            if not drain.done():
                entry.loss_count = min(entry.loss_count + 1, 2_147_483_647)
                raise TimeoutError(f"serial send did not complete: {self._port}")
            await drain
            await asyncio.wait((response, timeout), return_when=asyncio.FIRST_COMPLETED)
            if timeout.done() and not response.done():
                entry.loss_count = min(entry.loss_count + 1, 2_147_483_647)
                self.diagnostics["timeouts"] += 1
                logger.warning("serial response timeout port=%s device=%s", self._port, dev_number)
                return True
            if self._registry.get(dev_number) is not entry:
                self.diagnostics["stale"] += 1
                return False
            frame = response.result()
            if isinstance(decode_frame_by_funcode(frame), ExceptionResponse):
                entry.loss_count = min(entry.loss_count + 1, 2_147_483_647)
                self.diagnostics["exceptions"] += 1
                logger.warning("serial exception port=%s device=%s", self._port, dev_number)
                return False
            entry.last_back = time.time()
            entry.loss_count = 0
            if await self._ingest(dev_number, frame) and retry:
                self.diagnostics["retry_recovered"] += 1
                logger.warning("serial retry recovered port=%s device=%s", self._port, dev_number)
            return False
        finally:
            self._active = None
            session = self._session_map.get(dev_number)
            if session is not None and session.pending_read is pending:
                self._session_map.set_pending_read(dev_number, None)
            response.cancel()
            timeout.cancel()
            if drain is not None:
                drain.cancel()
            await asyncio.gather(
                timeout, *([drain] if drain is not None else []), return_exceptions=True
            )

    async def _ingest(self, dev_number: str, frame: bytes) -> bool:
        try:
            # Keep cancellation on this task: wait_for on Python 3.11 can consume
            # shutdown cancellation when ingestion completes in the same loop turn.
            async with asyncio.timeout(self._response_timeout_sec):
                await self._on_frame(dev_number, frame)
        except TimeoutError:
            self.diagnostics["ingestion_timeouts"] += 1
            logger.error("serial ingestion timeout port=%s device=%s", self._port, dev_number)
        except Exception:
            logger.exception("serial ingestion failed port=%s device=%s", self._port, dev_number)
        else:
            return True
        return False

    async def _sleep_until(self, deadline: float) -> None:
        await self._clock.sleep(max(0.0, deadline - self._clock.monotonic()))
