from __future__ import annotations

from ruisheng_gw.domain.device import Device
from ruisheng_gw.domain.point import Point
from ruisheng_gw.domain.registry import (
    AlarmSpec,
    PointEntry,
    Registry,
    RegistryEntry,
    ThresholdSpec,
)
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


class _AlarmRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def apply_alarm_reading(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
        return True


def test_relation_value_expires_instead_of_using_stale_cache() -> None:
    ingestor = FrameIngestor(
        registry=_registry(),
        batch=_Batch(),
        publisher=_Publisher(),
        relation_value_max_age_sec=30,
    )
    ingestor._current_values[("D1", 11)] = (7.0, 7.0, 100.0)  # noqa: SLF001
    alarm = AlarmSpec(
        id=1,
        reg_bit=None,
        alarm_name="linked",
        alarm_type=">",
        limit_value=5.0,
        alarm_msg=None,
        waring_flag=False,
        relation_point_id=11,
        relation_reg_bit=None,
        relation_alarm_type=">",
        relation_limit_value=1.0,
    )
    assert ingestor._relation_value("D1", alarm, observed_at=120.0) == 7.0  # noqa: SLF001
    assert ingestor._relation_value("D1", alarm, observed_at=131.0) is None  # noqa: SLF001
    assert ingestor._relation_value("D1", alarm, observed_at=99.0) is None  # noqa: SLF001


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


async def test_composite_alarm_uses_relation_value_from_same_frame() -> None:
    registry = _registry()
    entry = registry.get("D1")
    assert entry is not None
    relation_point = PointEntry(
        point=Point(
            point_id=11,
            dev_number="D1",
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=1,
            fun_code=3,
        )
    )
    main_point = PointEntry(
        point=Point(
            point_id=10,
            dev_number="D1",
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=0,
            fun_code=3,
        ),
        alarms=(
            AlarmSpec(
                id=7,
                reg_bit=None,
                alarm_name="composite",
                alarm_type=">",
                limit_value=10.0,
                alarm_msg=None,
                waring_flag=False,
                relation_point_id=11,
                relation_reg_bit=None,
                relation_alarm_type="=",
                relation_limit_value=5.0,
            ),
        ),
    )
    entry.points = {10: main_point, 11: relation_point}
    alarm_repository = _AlarmRepository()
    ingestor = FrameIngestor(
        registry=registry,
        batch=_Batch(),
        publisher=_Publisher(),
        alarm_repository=alarm_repository,
    )
    frame = append_crc_to_frame(bytes.fromhex("030304000b0005"))

    await ingestor.process_frame_for_pending(
        dev_number="D1",
        frame=frame,
        pending_read=PendingRead(
            dev_number="D1",
            fun_code=3,
            start_addr=0,
            quantity=2,
            points=(main_point, relation_point),
        ),
    )

    assert alarm_repository.calls[0]["value"] == 11.0
    assert alarm_repository.calls[0]["relation_value"] == 5.0


async def test_one_alarm_rule_failure_does_not_block_other_rules_or_telemetry() -> None:
    registry = _registry()
    entry = registry.get("D1")
    assert entry is not None
    events: list[str] = []

    def alarm(alarm_id: int) -> AlarmSpec:
        return AlarmSpec(
            id=alarm_id,
            reg_bit=None,
            alarm_name=f"rule-{alarm_id}",
            alarm_type=">",
            limit_value=10.0,
            alarm_msg=None,
            waring_flag=False,
            relation_point_id=None,
            relation_reg_bit=None,
            relation_alarm_type=None,
            relation_limit_value=None,
        )

    first_point = PointEntry(
        point=entry.points[10].point,
        alarms=(alarm(7), alarm(8)),
    )
    second_point = PointEntry(
        point=Point(
            point_id=11,
            dev_number="D1",
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=1,
        ),
        alarms=(alarm(9),),
    )
    entry.points = {10: first_point, 11: second_point}

    class OrderedBatch(_Batch):
        def submit(self, row: BatchRow) -> None:
            events.append(f"batch:{row.point_id}")
            super().submit(row)

    class OrderedPublisher(_Publisher):
        async def publish_realtime(self, ev: RealtimeEvent) -> None:
            events.append(f"realtime:{ev.point_id}")
            await super().publish_realtime(ev)

    class PartiallyFailingRepository(_AlarmRepository):
        async def apply_alarm_reading(self, **kwargs: object) -> bool:
            events.append(f"alarm:{kwargs['alarm_cfg_id']}")
            if kwargs["alarm_cfg_id"] == 7:
                raise OSError("one bad rule")
            return await super().apply_alarm_reading(**kwargs)

    batch = OrderedBatch()
    publisher = OrderedPublisher()
    repository = PartiallyFailingRepository()
    ingestor = FrameIngestor(
        registry=registry,
        batch=batch,
        publisher=publisher,
        alarm_repository=repository,
    )
    frame = append_crc_to_frame(bytes([0x03, 0x03, 0x04, 0x00, 0x1E, 0x00, 0x28]))

    await ingestor.process_frame_for_pending(
        dev_number="D1",
        frame=frame,
        pending_read=PendingRead(
            dev_number="D1",
            fun_code=3,
            start_addr=0,
            quantity=2,
            points=(first_point, second_point),
        ),
    )

    assert len(batch.rows) == 2
    assert len(publisher.realtime) == 2
    assert [call["alarm_cfg_id"] for call in repository.calls] == [8, 9]
    assert events == [
        "batch:10",
        "realtime:10",
        "batch:11",
        "realtime:11",
        "alarm:7",
        "alarm:8",
        "alarm:9",
    ]
