from __future__ import annotations

from ruisheng_gw.domain.device import Device
from ruisheng_gw.domain.point import Point
from ruisheng_gw.domain.registry import PointEntry, Registry, RegistryEntry, ThresholdSpec
from ruisheng_gw.ingest import FrameIngestor
from ruisheng_gw.persistence.batch_writer import BatchRow
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame
from ruisheng_gw.pubsub.schemas import AlarmEvent, RealtimeEvent
from ruisheng_gw.transport.session import PendingRead


class _Batch:
    def __init__(self) -> None:
        self.rows: list[BatchRow] = []

    def submit(self, row: BatchRow) -> None:
        self.rows.append(row)


class _Publisher:
    def __init__(self) -> None:
        self.realtime: list[RealtimeEvent] = []
        self.alarms: list[AlarmEvent] = []

    async def publish_realtime(self, ev: RealtimeEvent) -> None:
        self.realtime.append(ev)

    async def publish_alarm(self, ev: AlarmEvent) -> None:
        self.alarms.append(ev)


def _registry() -> Registry:
    reg = Registry()
    entry = RegistryEntry(
        device=Device(dev_number="D1", usr_group="ug"),
        update_interval_decisec=10,
        modbus_addr=3,
    )
    entry.points[10] = PointEntry(
        point=Point(
            point_id=10,
            dev_number="D1",
            point_ratio=0.5,
            point_offset=1.0,
            user_ratio=2.0,
            user_point_offset=0.0,
        ),
        threshold=ThresholdSpec(min_val=None, max_val=20.0, alarm_level=2),
    )
    reg._entries["D1"] = entry  # noqa: SLF001
    return reg


async def test_process_read_holding_response_submits_and_publishes() -> None:
    batch = _Batch()
    publisher = _Publisher()
    ingestor = FrameIngestor(registry=_registry(), batch=batch, publisher=publisher)
    frame = append_crc_to_frame(bytes([0x03, 0x03, 0x02, 0x00, 0x1E]))

    await ingestor.process_frame(dev_number="D1", frame=frame)

    assert len(batch.rows) == 1
    assert batch.rows[0].org_value == 30.0
    assert batch.rows[0].rt_value == 32.0
    assert publisher.realtime[0].point_id == 10
    assert publisher.alarms[0].level == 2


async def test_process_frame_ignores_wrong_slave() -> None:
    batch = _Batch()
    publisher = _Publisher()
    ingestor = FrameIngestor(registry=_registry(), batch=batch, publisher=publisher)
    frame = append_crc_to_frame(bytes([0x04, 0x03, 0x02, 0x00, 0x1E]))

    await ingestor.process_frame(dev_number="D1", frame=frame)

    assert batch.rows == []
    assert publisher.realtime == []


async def test_process_register_frame_marks_device_seen() -> None:
    registry = _registry()
    batch = _Batch()
    publisher = _Publisher()
    ingestor = FrameIngestor(registry=registry, batch=batch, publisher=publisher)
    frame = append_crc_to_frame(
        bytes([0xFE, 0x15])
        + b"SN-001".ljust(24, b"\x00")
        + b"1.2".ljust(5, b"\x00")
        + b"3".ljust(3, b"\x00")
    )

    await ingestor.process_frame(dev_number="D1", frame=frame)

    assert registry.get("D1").device.last_seen > 0  # type: ignore[union-attr]
    assert batch.rows == []


async def test_process_pending_register_bit_response_maps_by_address() -> None:
    registry = _registry()
    entry = registry.get("D1")
    assert entry is not None
    point_entry = PointEntry(
        point=Point(
            point_id=11,
            dev_number="D1",
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=12,
            fun_code=3,
            r_bit=1,
            value_type="bit",
        ),
        threshold=ThresholdSpec(min_val=None, max_val=None, alarm_level=1),
    )
    entry.points[11] = point_entry
    batch = _Batch()
    publisher = _Publisher()
    ingestor = FrameIngestor(registry=registry, batch=batch, publisher=publisher)
    frame = append_crc_to_frame(bytes.fromhex("0303020002"))

    await ingestor.process_frame_for_pending(
        dev_number="D1",
        frame=frame,
        pending_read=PendingRead(
            dev_number="D1",
            fun_code=3,
            start_addr=12,
            quantity=1,
            points=(point_entry,),
        ),
    )

    assert batch.rows[0].point_id == 11
    assert batch.rows[0].org_value == 1.0
    assert batch.rows[0].rt_value == 1.0


async def test_process_pending_double_word_response() -> None:
    registry = _registry()
    entry = registry.get("D1")
    assert entry is not None
    point_entry = PointEntry(
        point=Point(
            point_id=12,
            dev_number="D1",
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=20,
            fun_code=4,
            value_type="双字",
        ),
        threshold=ThresholdSpec(min_val=None, max_val=None, alarm_level=1),
    )
    entry.points[12] = point_entry
    batch = _Batch()
    publisher = _Publisher()
    ingestor = FrameIngestor(registry=registry, batch=batch, publisher=publisher)
    frame = append_crc_to_frame(bytes.fromhex("03040400010002"))

    await ingestor.process_frame_for_pending(
        dev_number="D1",
        frame=frame,
        pending_read=PendingRead(
            dev_number="D1",
            fun_code=4,
            start_addr=20,
            quantity=2,
            points=(point_entry,),
        ),
    )

    assert batch.rows[0].point_id == 12
    assert batch.rows[0].org_value == 65538.0


async def test_process_pending_coil_response() -> None:
    registry = _registry()
    entry = registry.get("D1")
    assert entry is not None
    point_entry = PointEntry(
        point=Point(
            point_id=13,
            dev_number="D1",
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=5,
            fun_code=1,
            value_type="bit",
        ),
        threshold=ThresholdSpec(min_val=None, max_val=None, alarm_level=1),
    )
    entry.points[13] = point_entry
    batch = _Batch()
    publisher = _Publisher()
    ingestor = FrameIngestor(registry=registry, batch=batch, publisher=publisher)
    frame = append_crc_to_frame(bytes.fromhex("03010120"))

    await ingestor.process_frame_for_pending(
        dev_number="D1",
        frame=frame,
        pending_read=PendingRead(
            dev_number="D1",
            fun_code=1,
            start_addr=0,
            quantity=8,
            points=(point_entry,),
        ),
    )

    assert batch.rows[0].point_id == 13
    assert batch.rows[0].org_value == 1.0
