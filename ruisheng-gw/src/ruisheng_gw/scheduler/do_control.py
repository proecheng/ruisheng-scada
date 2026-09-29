"""Explicit two-channel board profile; writes are never retried or replayed."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ruisheng_gw.protocol.exceptions import ProtocolError
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame, verify_crc16

if TYPE_CHECKING:
    from ruisheng_gw.domain.registry import RegistryEntry
    from ruisheng_gw.scheduler.serial_poller import SerialPoller

PROFILE = "two_channel_fc05_00ff_v1"
CHANNEL_MASK = 3
READ_FC = 3
WRITE_FC = 5
EXCEPTION_LENGTH = 5
READBACK_REGISTER_COUNT = 38
READBACK_BYTE_COUNT = READBACK_REGISTER_COUNT * 2
READBACK_LENGTH = READBACK_BYTE_COUNT + 5
DO_REGISTER_OFFSET = 1


@dataclass
class DoRequest:
    entry: RegistryEntry
    mask: int
    value: int
    deadline: float
    result: asyncio.Future[dict[str, object]]
    channels: list[dict[str, object]] = field(default_factory=list)


class DoController:
    """Run only inside the bus poller's transaction worker."""

    def __init__(self, poller: SerialPoller) -> None:
        self.poller = poller
        self.queue: asyncio.Queue[DoRequest] = asyncio.Queue(maxsize=8)
        self.active: tuple[bytes, asyncio.Future[bytes], float] | None = None

    async def submit(
        self, entry: RegistryEntry, mask: int, value: int, remaining: float = 12
    ) -> dict[str, object]:
        if type(mask) is not int or mask not in (1, 2, 3):
            raise ValueError("invalid DO selection")
        if type(value) is not int or value < 0 or value & ~mask:
            raise ValueError("invalid DO value")
        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        remaining = min(12, max(0, remaining))
        request = DoRequest(entry, mask, value, time.monotonic() + remaining, future)
        self.queue.put_nowait(request)
        return await asyncio.wait_for(future, timeout=remaining + 1)

    def receive(self, frame: bytes) -> bool:
        if self.active is None:
            return False
        sent, future, deadline = self.active
        if future.done() or time.monotonic() >= deadline:
            return True
        try:
            verify_crc16(frame)
        except ProtocolError:
            return True
        exception = len(frame) == EXCEPTION_LENGTH and frame[:2] == bytes([sent[0], sent[1] | 0x80])
        echoed = sent[1] == WRITE_FC and frame == sent
        read = (
            sent[1] == READ_FC
            and len(frame) == READBACK_LENGTH
            and frame[:3] == bytes([sent[0], READ_FC, READBACK_BYTE_COUNT])
        )
        if exception or echoed or read:
            future.set_result(frame)
        return True

    def close(self) -> None:
        while not self.queue.empty():
            request = self.queue.get_nowait()
            if not request.result.done():
                request.result.set_result({"status": "failed", "reason": "serial_disconnected"})

    def check(self, request: DoRequest) -> None:
        p = self.poller
        if request.result.done() or time.monotonic() >= request.deadline:
            raise TimeoutError("command_expired")
        if p._registry.get(request.entry.device.dev_number) is not request.entry:
            raise ValueError("device_configuration_changed")
        if p._writer.is_closing():
            raise ConnectionError("serial_disconnected")

    async def exchange(self, request: DoRequest, body: bytes) -> bytes:
        self.check(request)
        p = self.poller
        p._discard_input()
        sent = append_crc_to_frame(body)
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        deadline = min(request.deadline, time.monotonic() + p._response_timeout_sec)
        self.active = (sent, future, deadline)
        try:
            # No cancellation point between the last identity/deadline check and write.
            self.check(request)
            p._writer.write(sent)
            async with asyncio.timeout_at(deadline):
                await p._writer.drain()
                frame = await future
            if frame[1] & 0x80:
                raise ValueError(f"modbus_exception_{frame[2]}")
            return frame
        finally:
            self.active = None
            future.cancel()
            await asyncio.sleep(p._quiet_time_sec)

    async def readback(self, request: DoRequest) -> int:
        # This control profile is limited to zero_origin_38 devices. Use the same
        # proven read as polling: a nonzero-start subread may return the DI prefix.
        frame = await self.exchange(
            request,
            bytes([request.entry.modbus_addr, READ_FC, 0, 0, 0, READBACK_REGISTER_COUNT]),
        )
        offset = 3 + DO_REGISTER_OFFSET * 2
        value = int.from_bytes(frame[offset : offset + 2], "big")
        if value & ~CHANNEL_MASK:
            raise ValueError("readback_out_of_range")
        return value

    async def run_one(self) -> None:
        request = self.queue.get_nowait()
        if request.result.done():
            return
        result: dict[str, object] = {"status": "failed", "channels": request.channels}
        try:
            initial = await self.readback(request)
            result["before"] = initial
            expected = initial
            for channel in range(2):
                bit = 1 << channel
                if not request.mask & bit:
                    continue
                high = bool(request.value & bit)
                step: dict[str, object] = {"channel": channel + 1, "high": high, "phase": "sending"}
                request.channels.append(step)
                # The board uses 00FF for LOW; generic Modbus OFF remains 0000 elsewhere.
                value = 0xFF00 if high else 0x00FF
                body = bytes([request.entry.modbus_addr, 5, 0, channel]) + value.to_bytes(2, "big")
                await self.exchange(request, body)
                step["phase"] = "acknowledged"
                actual = await self.readback(request)
                expected = (expected | bit) if high else (expected & ~bit)
                result["readback"] = actual
                if actual != expected:
                    raise ValueError("readback_mismatch")
                step["phase"] = "verified"
            result["status"] = "success"
        except TimeoutError:
            result["status"] = "timeout"
            result["reason"] = "response_timeout_output_may_have_changed"
        except (OSError, ValueError) as exc:
            result["reason"] = str(exc)
        finally:
            if not request.result.done():
                request.result.set_result(result)
