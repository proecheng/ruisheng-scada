"""repository integration: bulk UPSERT point_data_realtime + INSERT point_data_history."""

from __future__ import annotations

import pytest
import pytest_asyncio
from ruisheng_gw.persistence.batch_writer import BatchRow
from ruisheng_gw.persistence.repository import Repository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def engine(postgres_url: str):
    """Uses Plan 0 E1 fixture `postgres_url` (session-scope container)."""
    eng = create_async_engine(postgres_url)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE point_data_realtime, point_data_history"))


async def test_bulk_insert_rows_appear(engine) -> None:
    repo = Repository(engine)
    rows = [
        BatchRow(
            dev_number="60270012",
            point_id=1,
            rt_value=10.0,
            org_value=100.0,
            recorded_at=1_700_000_000.0,
        ),
        BatchRow(
            dev_number="60270012",
            point_id=2,
            rt_value=20.0,
            org_value=200.0,
            recorded_at=1_700_000_000.0,
        ),
    ]
    await repo.flush(rows)
    # verify realtime UPSERT
    rt = await repo.fetch_realtime(dev_number="60270012")
    assert len(rt) == 2
    assert rt[0].rt_value in (10.0, 20.0)


async def test_upsert_overwrites_existing(engine) -> None:
    repo = Repository(engine)
    await repo.flush(
        [
            BatchRow(
                dev_number="60270012", point_id=1, rt_value=10.0, org_value=100.0, recorded_at=1.0
            )
        ]
    )
    await repo.flush(
        [
            BatchRow(
                dev_number="60270012", point_id=1, rt_value=99.0, org_value=100.0, recorded_at=2.0
            )
        ]
    )
    rt = await repo.fetch_realtime(dev_number="60270012")
    one = [r for r in rt if r.point_id == 1]
    assert len(one) == 1
    assert one[0].rt_value == 99.0
