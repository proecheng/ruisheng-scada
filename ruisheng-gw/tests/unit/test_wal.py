"""WAL file writes + rotate + drop-oldest + replay."""

from __future__ import annotations

from pathlib import Path

from ruisheng_gw.persistence.batch_writer import BatchRow
from ruisheng_gw.persistence.wal import Wal


async def test_append_writes_ndjson(tmp_path: Path) -> None:
    wal = Wal(wal_dir=str(tmp_path), single_file_mb=10, total_gb=1)
    rows = [
        BatchRow(dev_number="D", point_id=1, rt_value=1.0, org_value=1.0, recorded_at=0.0),
        BatchRow(dev_number="D", point_id=2, rt_value=2.0, org_value=2.0, recorded_at=0.0),
    ]
    await wal.append(rows)
    files = list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
    assert len(files) == 1
    content = files[0].read_text().splitlines()
    assert len(content) == 2
    import json

    j = json.loads(content[0])
    assert j["dev_number"] == "D"


async def test_rotate_on_size(tmp_path: Path) -> None:
    wal = Wal(wal_dir=str(tmp_path), single_file_mb=0.001, total_gb=1)  # ~1KB rotate
    many_rows = [
        BatchRow(dev_number="D", point_id=i, rt_value=float(i), org_value=0.0, recorded_at=0.0)
        for i in range(200)
    ]
    await wal.append(many_rows)
    files = list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
    assert len(files) >= 2  # rotated


async def test_drop_oldest_when_total_exceeds(tmp_path: Path) -> None:
    wal = Wal(wal_dir=str(tmp_path), single_file_mb=0.001, total_gb=0.00001)  # ~10KB total
    many_rows = [
        BatchRow(dev_number="D", point_id=i, rt_value=float(i), org_value=0.0, recorded_at=0.0)
        for i in range(500)
    ]
    await wal.append(many_rows)
    files = list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
    total_size = sum(f.stat().st_size for f in files)
    assert total_size <= 0.00002 * 1024 * 1024 * 1024 + 2048  # capped + some slack


async def test_replay_reads_all_files_then_deletes(tmp_path: Path) -> None:
    wal = Wal(wal_dir=str(tmp_path), single_file_mb=10, total_gb=1)
    await wal.append(
        [
            BatchRow(dev_number="D", point_id=1, rt_value=1.0, org_value=1.0, recorded_at=0.0),
        ]
    )
    replayed: list[BatchRow] = []

    async def _sink(rows: list[BatchRow]) -> None:
        replayed.extend(rows)

    await wal.replay_and_cleanup(sink=_sink)
    assert len(replayed) == 1
    assert not list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
