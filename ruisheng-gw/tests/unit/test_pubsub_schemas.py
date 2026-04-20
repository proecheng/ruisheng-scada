"""Pub/sub schemas: RealtimeEvent + AlarmEvent with schema_version."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from ruisheng_gw.pubsub.schemas import AlarmEvent, RealtimeEvent


def test_realtime_event_happy_path() -> None:
    ev = RealtimeEvent(
        schema_version=1,
        dev_number="D1",
        point_id=1,
        rt_value=10.0,
        org_value=100.0,
        recorded_at=1_700_000_000.0,
    )
    assert ev.schema_version == 1
    j = ev.model_dump_json()
    parsed = RealtimeEvent.model_validate_json(j)
    assert parsed == ev


def test_realtime_event_rejects_wrong_schema_version() -> None:
    with pytest.raises(ValidationError):
        RealtimeEvent(
            schema_version=2,
            dev_number="D1",
            point_id=1,
            rt_value=10.0,
            org_value=100.0,
            recorded_at=0.0,
        )


def test_alarm_event_happy_path() -> None:
    ev = AlarmEvent(
        schema_version=1,
        dev_number="D1",
        point_id=1,
        value=150.0,
        threshold=100.0,
        level=1,
        fired_at=1_700_000_000.0,
    )
    assert ev.level == 1
    parsed = AlarmEvent.model_validate_json(ev.model_dump_json())
    assert parsed == ev
