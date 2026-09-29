"""One-off, retained target-side experiment; payload/credentials arrive via stdin.

Only the uniquely named test database may be used. No serial I/O, Redis publish,
production DDL/DML, production container edits, or deletes are performed here.
"""

# Imports below must follow the in-memory code override; numeric literals are test cases.
# ruff: noqa: PLC0415, PLR2004

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine


class Publisher:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish_realtime(self, event: Any) -> None:
        self.events.append(event)

    async def publish_alarm(self, event: Any) -> None:
        raise AssertionError("Diagnostic points must not publish alarms")


async def verify(payload: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0915, PLR0912
    database = payload["test_database"]
    if not re.fullmatch(r"test_point_pipeline_20260907_[0-9a-f]{8}", database):
        raise ValueError("Not an isolated point-pipeline database")
    mode = payload["mode"]
    if mode not in {"deployed", "patched"}:
        raise ValueError("Unknown code mode")
    url = make_url(payload["database_url"])
    if url.database != "ruisheng" or url.username != "ruisheng_gw":
        raise ValueError("Unexpected source connection identity")
    engine = create_async_engine(url.set(database=database), hide_parameters=True)
    hashes = {}
    for name in ["ruisheng_gw.persistence.batch_writer", "ruisheng_gw.ingest"]:
        module = importlib.import_module(name)
        assert module.__file__ is not None
        deployed = await asyncio.to_thread(Path(module.__file__).read_bytes)
        hashes[name] = {"deployed": hashlib.sha256(deployed).hexdigest()}
        if mode == "patched":
            source = payload["sources"][name]
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
            if digest != payload["source_hashes"][name]:
                raise ValueError("Source integrity check failed")
            # Override only this disposable interpreter, never the running GW files.
            exec(compile(source, f"<diagnostic:{name}>", "exec"), module.__dict__)
            hashes[name]["tested"] = digest

    from ruisheng_gw.domain.registry import Registry, RegistryEntry
    from ruisheng_gw.ingest import FrameIngestor
    from ruisheng_gw.persistence.batch_writer import BatchRow, BatchWriter
    from ruisheng_gw.persistence.repository import Repository
    from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame
    from ruisheng_gw.scheduler.clock import RealClock
    from ruisheng_gw.transport.session import PendingRead
    from ruisheng_shared.models.timeseries import PointDataHistory, PointDataRealtime

    repository = Repository(engine)
    result: dict[str, Any] = {
        "mode": mode,
        "database": database,
        "source_hashes": hashes,
        "points": [],
    }
    try:
        async with engine.begin() as conn:
            actual = (await conn.execute(text("SELECT current_database()"))).scalar_one()
            if actual != database:
                raise ValueError("Database identity mismatch")
            if mode == "deployed":
                for table in [PointDataRealtime.__table__, PointDataHistory.__table__]:
                    await conn.run_sync(cast(Table, table).create)
                await conn.execute(
                    text(
                        "SELECT create_hypertable('point_data_history', 'recorded_at', "
                        "chunk_time_interval => INTERVAL '1 month')"
                    )
                )
            result["hypertable"] = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM timescaledb_information.hypertables "
                        "WHERE hypertable_name = 'point_data_history'"
                    )
                )
            ).scalar_one() == 1

        def point_row(
            candidate: dict[str, Any],
            index: int,
            dev: str,
            *,
            legacy: bool = False,
            raw: bool = False,
        ) -> dict[str, Any]:
            return {
                "id": index,
                "dev_number": dev,
                "point_number": candidate["address"],
                "fun_code": candidate["fc"],
                "dev_addr": 1,
                "value_type": candidate["value_type"] if legacy else "\u5b57",
                "r_bit": candidate["bit"] if candidate["bit"] >= 0 else None,
                "point_ratio": 1.0 if raw else candidate["ratio"],
                "point_offset": 0.0 if raw else candidate["point_offset"],
                "user_ratio": 1.0 if raw else candidate["user_ratio"],
                "user_point_offset": 0.0 if raw else candidate["user_offset"],
            }

        def setup(
            dev: str, rows: list[dict[str, Any]]
        ) -> tuple[RegistryEntry, BatchWriter, Publisher, FrameIngestor]:
            registry = Registry.build(
                device_rows=[
                    {
                        "dev_number": dev,
                        "usr_group": "diagnostic-only",
                        "update_interval_decisec": 10,
                        "modbus_addr": 1,
                    }
                ],
                point_rows=rows,
            )
            writer = BatchWriter(sink=repository, clock=RealClock(), flush_interval_sec=0.01)
            publisher = Publisher()
            ingest = FrameIngestor(registry=registry, batch=writer, publisher=publisher)
            entry = registry.get(dev)
            assert entry is not None
            return entry, writer, publisher, ingest

        async def finish(writer: BatchWriter) -> None:
            writer.stop()
            await asyncio.wait_for(writer.run(), timeout=20)
            if writer.stats["flush_error_total"] or writer.stats["drop_total"]:
                raise AssertionError("Unexpected batch persistence failure")

        async def readback(dev: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            async with engine.connect() as conn:
                realtime = [
                    dict(row)
                    for row in (
                        await conn.execute(
                            text(
                                "SELECT point_id, org_value, rt_value, recorded_at "
                                "FROM point_data_realtime WHERE dev_number = :dev ORDER BY point_id"
                            ),
                            {"dev": dev},
                        )
                    ).mappings()
                ]
                history = [
                    dict(row)
                    for row in (
                        await conn.execute(
                            text(
                                "SELECT point_id, org_value, rt_value, recorded_at "
                                "FROM point_data_history WHERE dev_number = :dev "
                                "ORDER BY point_id, recorded_at"
                            ),
                            {"dev": dev},
                        )
                    ).mappings()
                ]
            return realtime, history

        def frame(raw: int, fc: int = 3) -> bytes:
            body = bytes([1, fc, 2]) + raw.to_bytes(2, "big")
            return append_crc_to_frame(body)

        def values_equal(row: dict[str, Any], raw: float, scaled: float) -> bool:
            return row["org_value"] == raw and math.isclose(row["rt_value"], scaled)

        # Real captured frames: 15 unique addresses, raw values only, no units.
        physical_dev = f"{mode}-physical-raw"
        physical = []
        for sample in payload["physical_samples"]:
            for offset, raw_value in enumerate(sample["registers"]):
                address = sample["start_address"] + offset
                physical.append((address, raw_value))
        raw_rows = [
            point_row({"address": a, "fc": 3, "bit": -1}, a + 1, physical_dev, raw=True)
            for a, _ in physical
        ]
        entry, writer, publisher, ingest = setup(physical_dev, raw_rows)
        for sample in payload["physical_samples"]:
            start, count = sample["start_address"], len(sample["registers"])
            points = tuple(
                p for p in entry.points.values() if start <= p.point.point_number < start + count
            )
            await ingest.process_frame_for_pending(
                dev_number=physical_dev,
                frame=bytes.fromhex(sample["rx_hex"]),
                pending_read=PendingRead(physical_dev, 3, start, count, points),
            )
        await finish(writer)
        realtime, history = await readback(physical_dev)
        result["physical_replay"] = [
            {
                "address": a,
                "raw": raw,
                "realtime_pass": len(
                    [r for r in realtime if r["point_id"] == a + 1 and values_equal(r, raw, raw)]
                )
                == 1,
                "history_pass": len(
                    [r for r in history if r["point_id"] == a + 1 and values_equal(r, raw, raw)]
                )
                == 1,
            }
            for a, raw in physical
        ]
        result["physical_counts"] = {
            "realtime": len(realtime),
            "history": len(history),
            "publisher_events": len(publisher.events),
        }

        # Positive synthetic values test addressing/scaling only, not signed semantics.
        candidates = payload["candidates"]
        for index, candidate in enumerate(candidates, 1):
            dev = f"{mode}-synthetic-{candidate['id']}"
            row = point_row(candidate, index, dev, legacy=candidate["fc"] != 3)
            entry, writer, publisher, ingest = setup(dev, [row])
            point = entry.points[index]
            expected_values = []
            for raw in [1000 + index, 2000 + index]:
                response = (
                    frame(raw) if candidate["fc"] == 3 else append_crc_to_frame(bytes([1, 1, 1, 3]))
                )
                await ingest.process_frame_for_pending(
                    dev_number=dev,
                    frame=response,
                    pending_read=PendingRead(
                        dev, candidate["fc"], candidate["address"], 1, (point,)
                    ),
                )
                expected_values.append(
                    (
                        raw,
                        (raw * candidate["ratio"] + candidate["point_offset"])
                        * candidate["user_ratio"]
                        + candidate["user_offset"],
                    )
                )
                await asyncio.sleep(0.002)
            await finish(writer)
            realtime, history = await readback(dev)
            item = {
                "id": candidate["id"],
                "fc": candidate["fc"],
                "address": candidate["address"],
                "realtime_count": len(realtime),
                "history_count": len(history),
                "publisher_events": len(publisher.events),
            }
            if candidate["fc"] == 3:
                item.update(
                    {
                        "test_type": "synthetic-unsigned-with-candidate-scaling",
                        "realtime_pass": len(realtime) == 1
                        and values_equal(realtime[0], *expected_values[-1]),
                        "history_pass": len(history) == 2
                        and all(
                            values_equal(r, *values)
                            for r, values in zip(history, expected_values, strict=True)
                        ),
                        "expected": expected_values,
                        "realtime": realtime,
                        "history": history,
                    }
                )
            else:
                item.update(
                    {
                        "test_type": "ambiguous-coil-config-rejection",
                        "rejection_pass": not realtime and not history and not publisher.events,
                    }
                )
            result["points"].append(item)

        # Every legacy FC3 type must be rejected, including 0xffff (not +65535).
        dev = f"{mode}-legacy-s16"
        rows = [
            point_row(c, i, dev, legacy=True) for i, c in enumerate(candidates, 1) if c["fc"] == 3
        ]
        entry, writer, publisher, ingest = setup(dev, rows)
        for point in entry.points.values():
            await ingest.process_frame_for_pending(
                dev_number=dev,
                frame=frame(65535),
                pending_read=PendingRead(dev, 3, point.point.point_number, 1, (point,)),
            )
        await finish(writer)
        realtime, history = await readback(dev)
        written = {r["point_id"] for r in realtime}
        result["legacy_types"] = [
            {"id": c["id"], "rejection_pass": i not in written}
            for i, c in enumerate(candidates, 1)
            if c["fc"] == 3
        ]
        result["legacy_counts"] = {"realtime": len(realtime), "history": len(history)}

        # Cross-function contamination and malformed CRC must never write rows.
        for case in ["wrong-function", "bad-crc"]:
            dev = f"{mode}-{case}"
            row = point_row(candidates[0], 1, dev)
            if case == "wrong-function":
                row["fun_code"] = 4
            entry, writer, publisher, ingest = setup(dev, [row])
            response = frame(12)
            if case == "bad-crc":
                response = response[:-1] + bytes([response[-1] ^ 1])
            await ingest.process_frame_for_pending(
                dev_number=dev,
                frame=response,
                pending_read=PendingRead(dev, 3, 0, 1, (entry.points[1],)),
            )
            await finish(writer)
            realtime, history = await readback(dev)
            result[case] = {
                "rejection_pass": not realtime and not history,
                "realtime_count": len(realtime),
                "history_count": len(history),
            }

        # Deterministically complete queue.get and stop in the same event-loop turn.
        dev = f"{mode}-shutdown-race"
        writer = BatchWriter(sink=repository, clock=RealClock())
        entered = asyncio.Event()

        class StopDuringReadQueue(asyncio.Queue[BatchRow]):
            async def get(self) -> BatchRow:
                entered.set()
                row = await super().get()
                writer.stop()
                return row

        writer._queue = StopDuringReadQueue()
        task = asyncio.create_task(writer.run())
        await asyncio.wait_for(entered.wait(), 5)
        stamp = datetime.now(UTC).timestamp()
        writer.submit(BatchRow(dev, 1, 3.0, 3.0, stamp))
        await asyncio.wait_for(task, 20)
        realtime, history = await readback(dev)
        result["shutdown-race"] = {
            "realtime_count": len(realtime),
            "history_count": len(history),
            "pass": len(realtime) == 1 and len(history) == 1,
        }

        dev = f"{mode}-shutdown-drain"
        writer = BatchWriter(sink=repository, clock=RealClock())
        for i in range(46):
            writer.submit(BatchRow(dev, i, float(i), float(i), stamp))
        await finish(writer)
        realtime, history = await readback(dev)
        result["shutdown-drain"] = {
            "realtime_count": len(realtime),
            "history_count": len(history),
            "pass": len(realtime) == 46 and len(history) == 46,
        }

        # A history uniqueness failure must roll back the realtime UPSERT as well.
        dev = f"{mode}-transaction-rollback"
        original = BatchRow(dev, 1, 7.0, 7.0, stamp)
        await repository.flush([original])
        rejected = False
        try:
            await repository.flush([replace(original, rt_value=99.0, org_value=99.0)])
        except IntegrityError:
            rejected = True
        realtime, history = await readback(dev)
        result["transaction-rollback"] = {
            "pass": rejected
            and len(realtime) == 1
            and len(history) == 1
            and values_equal(realtime[0], 7.0, 7.0)
            and values_equal(history[0], 7.0, 7.0)
        }
        async with engine.connect() as conn:
            result["database_counts"] = dict(
                (
                    await conn.execute(
                        text(
                            "SELECT (SELECT count(*) FROM point_data_realtime) AS realtime, "
                            "(SELECT count(*) FROM point_data_history) AS history"
                        )
                    )
                )
                .mappings()
                .one()
            )
        result["completed_at"] = datetime.now(UTC).isoformat()
        return result
    finally:
        await engine.dispose()


def run(payload: dict[str, Any]) -> None:
    try:
        report = asyncio.run(verify(payload))
        print(json.dumps(report, ensure_ascii=True, default=str))
    except Exception as error:
        # Connection strings stay private even on a failed diagnostic run.
        print(json.dumps({"error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    run(json.load(sys.stdin))
