"""Frame ingestion: ModBus response frames -> persistence rows + Redis events."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from ruisheng_gw.domain.alarm_simple import check_threshold
from ruisheng_gw.domain.device import DeviceState
from ruisheng_gw.domain.point import ScalingError, apply_scaling
from ruisheng_gw.domain.registry import Registry
from ruisheng_gw.persistence.batch_writer import BatchRow
from ruisheng_gw.protocol.exceptions import ProtocolError
from ruisheng_gw.protocol.frames import (
    AnyFrame,
    HeartbeatFrame,
    ReadHoldingResponse,
    RegisterFrame,
    decode_frame_by_funcode,
)
from ruisheng_gw.pubsub.schemas import AlarmEvent, RealtimeEvent
from ruisheng_gw.transport.session import PendingRead

if TYPE_CHECKING:
    from ruisheng_gw.domain.registry import PointEntry

MAX_REGISTER_BIT = 15


class _BatchSink(Protocol):
    def submit(self, row: BatchRow) -> None: ...


class _Publisher(Protocol):
    async def publish_realtime(self, ev: RealtimeEvent) -> None: ...
    async def publish_alarm(self, ev: AlarmEvent) -> None: ...


class FrameIngestor:
    def __init__(
        self,
        *,
        registry: Registry,
        batch: _BatchSink,
        publisher: _Publisher,
    ) -> None:
        self._registry = registry
        self._batch = batch
        self._publisher = publisher

    async def process_frame(self, *, dev_number: str, frame: bytes) -> None:
        await self.process_frame_for_pending(dev_number=dev_number, frame=frame, pending_read=None)

    async def process_frame_for_pending(
        self,
        *,
        dev_number: str,
        frame: bytes,
        pending_read: PendingRead | None,
    ) -> None:
        entry = self._registry.get(dev_number)
        decoded = self._decode_frame(frame)
        if entry is None or decoded is None:
            return
        if isinstance(decoded, RegisterFrame):
            self._mark_seen(dev_number=dev_number, now=time.time())
            return
        if decoded.slave != entry.modbus_addr:
            return
        now = time.time()
        self._mark_seen(dev_number=dev_number, now=now)
        if isinstance(decoded, HeartbeatFrame):
            return
        if not isinstance(decoded, ReadHoldingResponse):
            return

        points, start_addr = _points_for_response(
            dev_number=dev_number,
            entry_points=entry.points,
            response=decoded,
            pending_read=pending_read,
        )
        for point_entry in points:
            raw_value = _raw_value_for_point(
                point_entry=point_entry,
                response=decoded,
                start_addr=start_addr,
            )
            if raw_value is None:
                continue
            try:
                _, rt_value = apply_scaling(point_entry.point, raw=raw_value)
            except ScalingError:
                continue
            row = BatchRow(
                dev_number=dev_number,
                point_id=point_entry.point.point_id,
                rt_value=rt_value,
                org_value=raw_value,
                recorded_at=now,
            )
            self._batch.submit(row)
            await self._publisher.publish_realtime(
                RealtimeEvent(
                    dev_number=row.dev_number,
                    point_id=row.point_id,
                    rt_value=row.rt_value,
                    org_value=row.org_value,
                    recorded_at=row.recorded_at,
                )
            )
            alarm = check_threshold(
                dev_number=row.dev_number,
                point_id=row.point_id,
                value=row.rt_value,
                spec=point_entry.threshold,
                now=now,
            )
            if alarm is not None:
                await self._publisher.publish_alarm(AlarmEvent(**alarm.__dict__))

    @staticmethod
    def _decode_frame(frame: bytes) -> AnyFrame | None:
        try:
            return decode_frame_by_funcode(frame)
        except ProtocolError:
            return None

    def _mark_seen(self, *, dev_number: str, now: float) -> None:
        entry = self._registry.get(dev_number)
        if entry is None:
            return
        if entry.device.state is DeviceState.ONLINE:
            entry.device.heartbeat(now=now)
        else:
            entry.device.register(now=now)


def _raw_value_for_point(
    *,
    point_entry: PointEntry,
    response: ReadHoldingResponse,
    start_addr: int,
) -> float | None:
    point = point_entry.point
    index = point.point_number - start_addr
    if index < 0 or index >= len(response.bits + response.registers):
        return None
    if response.fun_code in (1, 2):
        return _bit_response_value(response=response, index=index)
    return _register_response_value(
        response=response, index=index, value_type=point.value_type, bit=point.r_bit
    )


def _points_for_response(
    *,
    dev_number: str,
    entry_points: Mapping[int, PointEntry],
    response: ReadHoldingResponse,
    pending_read: PendingRead | None,
) -> tuple[list[PointEntry], int]:
    if pending_read is None:
        return [entry_points[k] for k in sorted(entry_points)], 0
    if pending_read.fun_code != response.fun_code or pending_read.dev_number != dev_number:
        return [], pending_read.start_addr
    return list(pending_read.points), pending_read.start_addr


def _bit_response_value(*, response: ReadHoldingResponse, index: int) -> float | None:
    if index >= len(response.bits):
        return None
    return float(response.bits[index])


def _double_word_value(*, response: ReadHoldingResponse, index: int) -> float | None:
    if index + 1 >= len(response.registers):
        return None
    return float((response.registers[index] << 16) | response.registers[index + 1])


def _register_bit_value(*, register: int, bit: int | None) -> float | None:
    if bit is None or bit < 0 or bit > MAX_REGISTER_BIT:
        return None
    return float((register >> bit) & 0x01)


def _register_response_value(
    *,
    response: ReadHoldingResponse,
    index: int,
    value_type: str,
    bit: int | None,
) -> float | None:
    if index >= len(response.registers):
        return None
    if value_type == "双字":
        return _double_word_value(response=response, index=index)
    if value_type == "bit":
        return _register_bit_value(register=response.registers[index], bit=bit)
    return float(response.registers[index])
