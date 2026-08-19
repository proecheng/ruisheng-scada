"""SQLAlchemy 2.0 async repository — bulk UPSERT realtime + INSERT history.

Plan 1 uses gw BYPASSRLS role (OQ-3 A). All write/read queries must
include `usr_group` filter if the table has one (12 FORCE RLS tables
per spec §3.7) — enforced by CI lint in E8, NOT at runtime here.

point_data_realtime does NOT have usr_group column (see shared ORM);
history same. Tenant isolation is via FK to devices.dev_number + UQ.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ruisheng_gw.persistence.batch_writer import BatchRow

STREAM_ALARM_FIRED = "stream:alarm:fired"
STREAM_ALARM_MAXLEN = 100_000


@dataclass(frozen=True)
class RealtimeRow:
    dev_number: str
    point_id: int
    org_value: float | None
    rt_value: float | None
    recorded_at: datetime


class Repository:
    def __init__(self, engine: AsyncEngine, *, redis: Any | None = None) -> None:
        self._engine = engine
        self._redis = redis

    async def flush(self, rows: list[BatchRow]) -> None:
        """Bulk UPSERT point_data_realtime + INSERT point_data_history in one tx."""
        if not rows:
            return
        async with self._engine.begin() as conn:
            # UPSERT realtime
            await conn.execute(
                text(  # noqa: TNL001 (table has no usr_group column)
                    """
                INSERT INTO point_data_realtime
                    (dev_number, point_id, org_value, rt_value, recorded_at)
                VALUES
                    (:dev_number, :point_id, :org_value, :rt_value, :recorded_at)
                ON CONFLICT (dev_number, point_id) DO UPDATE
                SET org_value = EXCLUDED.org_value,
                    rt_value = EXCLUDED.rt_value,
                    recorded_at = EXCLUDED.recorded_at
                    """
                ),
                [self._to_mapping(r) for r in rows],
            )
            # INSERT history
            await conn.execute(
                text(  # noqa: TNL001 (table has no usr_group column)
                    """
                INSERT INTO point_data_history
                    (dev_number, point_id, org_value, rt_value, recorded_at)
                VALUES
                    (:dev_number, :point_id, :org_value, :rt_value, :recorded_at)
                    """
                ),
                [self._to_mapping(r) for r in rows],
            )

    async def fetch_realtime(self, *, dev_number: str) -> list[RealtimeRow]:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT dev_number, point_id, org_value, rt_value, recorded_at
                FROM point_data_realtime
                WHERE dev_number = :dev_number
                ORDER BY point_id
            """),
                {"dev_number": dev_number},
            )
            return [
                RealtimeRow(
                    dev_number=row.dev_number,
                    point_id=row.point_id,
                    org_value=row.org_value,
                    rt_value=row.rt_value,
                    recorded_at=row.recorded_at,
                )
                for row in result
            ]

    async def apply_alarm_reading(
        self,
        *,
        alarm_cfg_id: int,
        value: float,
        observed_at: float,
        relation_value: float | None = None,
    ) -> bool:
        observed = datetime.fromtimestamp(observed_at, tz=UTC)
        async with self._engine.begin() as conn:
            cfg = (
                (
                    await conn.execute(
                        text(
                            "SELECT c.id, c.dev_number, c.point_id, c.alarm_name, c.alarm_msg, "
                            "       c.alarm_type, c.limit_value, c.waring_flag, "
                            "       c.relation_point_id, c.relation_alarm_type, "
                            "       c.relation_limit_value, d.usr_group, "
                            "       d.update_flag AS config_version "
                            "FROM device_waring_cfgs c "
                            "JOIN devices d ON d.dev_number = c.dev_number "
                            "WHERE c.id = :cfg AND c.enable IS TRUE"
                        ),
                        {"cfg": alarm_cfg_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if cfg is None:
                return False
            condition = await self._alarm_condition(
                cfg=dict(cfg), value=value, relation_value=relation_value
            )
            if condition is None:
                return False
            if not condition:
                transitioned = await conn.execute(
                    text(
                        "UPDATE device_waring_cfgs SET waring_flag = FALSE "
                        "WHERE id = :cfg AND enable IS TRUE AND waring_flag IS TRUE"
                    ),
                    {"cfg": alarm_cfg_id},
                )
                if transitioned.rowcount != 1:
                    return False
                await self._reset_lx_counters(
                    str(cfg["dev_number"]),
                    int(cfg["id"]),
                    config_version=int(cfg["config_version"]),
                )
                await conn.execute(
                    text(
                        "UPDATE alarm_records SET reset_at = :observed "
                        "WHERE id = (SELECT id FROM alarm_records "
                        "            WHERE alarm_cfg_id = :cfg AND reset_at IS NULL "
                        "            ORDER BY triggered_at DESC LIMIT 1) "
                        "AND reset_at IS NULL"
                    ),
                    {"cfg": alarm_cfg_id, "observed": observed},
                )
                return True
            transitioned = await conn.execute(
                text(
                    "UPDATE device_waring_cfgs SET waring_flag = TRUE "
                    "WHERE id = :cfg AND enable IS TRUE AND waring_flag IS FALSE"
                ),
                {"cfg": alarm_cfg_id},
            )
            if transitioned.rowcount != 1:
                return False
            alarm_id = (
                await conn.execute(
                    text(
                        "INSERT INTO alarm_records "
                        "(alarm_cfg_id, dev_number, point_id, alarm_name, alarm_msg, "
                        " alarm_value, alarm_type, limit_value, channels_sent, "
                        " triggered_at, usr_group) "
                        "VALUES (:cfg, :dev, :point, :name, :msg, :value, :kind, :limit, "
                        "        CAST('{}' AS JSONB), :observed, :tenant) RETURNING id"
                    ),
                    {
                        "cfg": alarm_cfg_id,
                        "dev": cfg["dev_number"],
                        "point": cfg["point_id"],
                        "name": cfg["alarm_name"],
                        "msg": cfg["alarm_msg"],
                        "value": value,
                        "kind": cfg["alarm_type"],
                        "limit": cfg["limit_value"],
                        "observed": observed,
                        "tenant": cfg["usr_group"],
                    },
                )
            ).scalar_one()
            payload = {
                "schema_version": 2,
                "event_id": alarm_id,
                "triggered_at": observed.isoformat(),
                "alarm_cfg_id": alarm_cfg_id,
                "dev_number": cfg["dev_number"],
                "point_id": cfg["point_id"],
                "value": value,
                "limit": float(cfg["limit_value"]),
            }
            await conn.execute(
                text(
                    "INSERT INTO alarm_outbox (alarm_id, payload, published) "
                    "VALUES (:alarm, CAST(:payload AS JSONB), FALSE)"
                ),
                {"alarm": alarm_id, "payload": json.dumps(payload)},
            )
            return True

    async def _alarm_condition(  # noqa: PLR0911
        self, *, cfg: dict[str, Any], value: float, relation_value: float | None
    ) -> bool | None:
        raw_config_version = cfg.get("config_version")
        config_version = int(raw_config_version) if raw_config_version is not None else None
        relation_point_id = cfg.get("relation_point_id")
        if relation_point_id is not None:
            relation_type = cfg.get("relation_alarm_type")
            relation_limit = cfg.get("relation_limit_value")
            if relation_value is None or relation_type is None or relation_limit is None:
                return None
            if relation_type == "LX":
                required_count = _lx_required_count(relation_limit)
                if required_count is None:
                    return None
                relation_matches = await self._lx_matches(
                    dev_number=str(cfg["dev_number"]),
                    alarm_cfg_id=int(cfg["id"]),
                    value=relation_value,
                    required_count=required_count,
                    counter="relation",
                    config_version=config_version,
                )
            else:
                relation_matches = _alarm_matches(
                    relation_value, str(relation_type), float(relation_limit)
                )
            if relation_matches is None:
                return None
            if not relation_matches:
                await self._reset_lx_counter(
                    str(cfg["dev_number"]),
                    int(cfg["id"]),
                    counter="main",
                    config_version=config_version,
                )
                return False
        alarm_type = str(cfg["alarm_type"])
        if alarm_type != "LX":
            return _alarm_matches(value, alarm_type, float(cfg["limit_value"]))
        required_count = _lx_required_count(cfg["limit_value"])
        if required_count is None:
            return None
        return await self._lx_matches(
            dev_number=str(cfg["dev_number"]),
            alarm_cfg_id=int(cfg["id"]),
            value=value,
            required_count=required_count,
            config_version=config_version,
        )

    async def _lx_matches(
        self,
        *,
        dev_number: str,
        alarm_cfg_id: int,
        value: float,
        required_count: int,
        counter: str = "main",
        config_version: int | None = None,
    ) -> bool | None:
        if self._redis is None:
            return None
        key = f"lx_counter:{dev_number}"
        if config_version is not None:
            key = f"{key}:{config_version}"
        field = f"{alarm_cfg_id}:{counter}"
        if not bool(value):
            await self._redis.hdel(key, field)
            return False
        count = int(await self._redis.hincrby(key, field, 1))
        if count > required_count:
            await self._redis.hset(key, field, required_count)
            count = required_count
        return count >= required_count

    async def _reset_lx_counters(
        self, dev_number: str, alarm_cfg_id: int, *, config_version: int | None = None
    ) -> None:
        if self._redis is not None:
            key = f"lx_counter:{dev_number}"
            if config_version is not None:
                key = f"{key}:{config_version}"
            await self._redis.hdel(
                key,
                f"{alarm_cfg_id}:main",
                f"{alarm_cfg_id}:relation",
            )

    async def _reset_lx_counter(
        self,
        dev_number: str,
        alarm_cfg_id: int,
        *,
        counter: str,
        config_version: int | None = None,
    ) -> None:
        if self._redis is not None:
            key = f"lx_counter:{dev_number}"
            if config_version is not None:
                key = f"{key}:{config_version}"
            await self._redis.hdel(key, f"{alarm_cfg_id}:{counter}")

    async def reset_lx_counters_for_devices(self, dev_numbers: set[str]) -> None:
        if self._redis is None or not dev_numbers:
            return
        keys: list[Any] = [f"lx_counter:{dev_number}" for dev_number in dev_numbers]
        for dev_number in dev_numbers:
            keys.extend(
                [key async for key in self._redis.scan_iter(match=f"lx_counter:{dev_number}:*")]
            )
        await self._redis.delete(*keys)

    async def relay_alarm_outbox_once(self, redis: Any, *, batch: int = 100) -> int:
        await redis.ping()
        relayed = 0
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, payload FROM alarm_outbox "
                            "WHERE published IS FALSE ORDER BY id "
                            "LIMIT :batch"
                        ),
                        {"batch": batch},
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            fields = {key: str(value) for key, value in row["payload"].items() if value is not None}
            await redis.xadd(
                STREAM_ALARM_FIRED,
                fields,
                maxlen=STREAM_ALARM_MAXLEN,
                approximate=True,
            )
            async with self._engine.begin() as conn:
                updated = await conn.execute(
                    text(
                        "UPDATE alarm_outbox SET published = TRUE "
                        "WHERE id = :id AND published IS FALSE"
                    ),
                    {"id": row["id"]},
                )
                if updated.rowcount == 1:
                    relayed += 1
        return relayed

    async def count_pending_alarm_outbox(self) -> int:
        async with self._engine.begin() as conn:
            return int(
                (
                    await conn.execute(
                        text("SELECT count(*) FROM alarm_outbox WHERE published IS FALSE")
                    )
                ).scalar_one()
            )

    @staticmethod
    def _to_mapping(r: BatchRow) -> dict[str, Any]:
        return {
            "dev_number": r.dev_number,
            "point_id": r.point_id,
            "org_value": r.org_value,
            "rt_value": r.rt_value,
            "recorded_at": datetime.fromtimestamp(r.recorded_at, tz=UTC),
        }


def _alarm_matches(value: float, alarm_type: str, limit_value: float) -> bool:
    if alarm_type == ">":
        return value > limit_value
    if alarm_type == "<":
        return value < limit_value
    if alarm_type == "=":
        return value == limit_value
    if alarm_type == "!=":
        return value != limit_value
    return False


def _lx_required_count(value: Any) -> int | None:
    try:
        count = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(count) or count <= 0 or not count.is_integer():
        return None
    return int(count)
