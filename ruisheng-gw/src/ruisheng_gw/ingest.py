"""Frame ingestion: ModBus response frames -> persistence rows + Redis events."""

from __future__ import annotations

import time
from typing import Protocol

from ruisheng_gw.domain.alarm_simple import check_threshold
from ruisheng_gw.domain.device import DeviceState
from ruisheng_gw.domain.point import ScalingError, apply_scaling
from ruisheng_gw.domain.registry import Registry
from ruisheng_gw.persistence.batch_writer import BatchRow
from ruisheng_gw.protocol.exceptions import ProtocolError
from ruisheng_gw.protocol.frames import (
    HeartbeatFrame,
    ReadHoldingResponse,
    RegisterFrame,
    decode_frame_by_funcode,
)
from ruisheng_gw.pubsub.schemas import AlarmEvent, RealtimeEvent


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
        entry = self._registry.get(dev_number)
        if entry is None:
            return
        now = time.time()
        try:
            decoded = decode_frame_by_funcode(frame)
        except ProtocolError:
            return
        if isinstance(decoded, RegisterFrame):
            self._mark_seen(dev_number=dev_number, now=now)
            return
        if decoded.slave != entry.modbus_addr:
            return
        self._mark_seen(dev_number=dev_number, now=now)
        if isinstance(decoded, HeartbeatFrame):
            return
        if not isinstance(decoded, ReadHoldingResponse):
            return

        points = [entry.points[k] for k in sorted(entry.points)]
        for point_entry, raw_register in zip(points, decoded.registers, strict=False):
            raw_value = float(raw_register)
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

    def _mark_seen(self, *, dev_number: str, now: float) -> None:
        entry = self._registry.get(dev_number)
        if entry is None:
            return
        if entry.device.state is DeviceState.ONLINE:
            entry.device.heartbeat(now=now)
        else:
            entry.device.register(now=now)
