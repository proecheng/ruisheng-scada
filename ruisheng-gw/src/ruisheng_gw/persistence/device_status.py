"""Persist communication observations independently of telemetry/Redis publishing."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from ruisheng_shared.device_status import SERIAL_LOSS_LIMIT, serial_response_is_recent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ruisheng_gw.domain.registry import Registry

logger = logging.getLogger(__name__)


async def sync_serial_status(engine: AsyncEngine, registry: Registry, *, now: float) -> None:
    observations = []
    for entry in registry.entries():
        if entry.transport_type != "serial":
            continue
        observations.append(
            {
                "dev_number": entry.device.dev_number,
                "usr_group": entry.device.usr_group,
                "version": entry.config_version,
                "port": entry.serial_port,
                "address": entry.modbus_addr,
                "last_call": datetime.fromtimestamp(entry.last_call, UTC)
                if entry.last_call
                else None,
                "last_back": datetime.fromtimestamp(entry.last_back, UTC)
                if entry.last_back
                else None,
                "loss_count": entry.loss_count,
                "online": entry.loss_count < SERIAL_LOSS_LIMIT
                and serial_response_is_recent(
                    last_back=entry.last_back,
                    now=now,
                    interval_decisec=entry.update_interval_decisec,
                ),
            }
        )
    if not observations:
        return
    async with engine.begin() as conn:
        # A delayed observation must not revive a disabled/deleted/reconfigured device.
        # Runtime writes never bump the configuration version. The normal timestamp
        # trigger still records real row changes; avoid writes for unchanged observations.
        await conn.execute(
            text("""
            UPDATE devices
            SET is_online = :online,
                last_call_at = COALESCE(:last_call, last_call_at),
                last_back_at = COALESCE(:last_back, last_back_at),
                loss_count = :loss_count
            WHERE dev_number = :dev_number AND usr_group = :usr_group
              AND update_flag = :version AND transport_type = 'serial'
              AND serial_port = :port AND modbus_addr = :address
              AND deleted_at IS NULL AND is_enabled IS TRUE
              AND (is_online IS DISTINCT FROM :online
                   OR last_call_at IS DISTINCT FROM COALESCE(:last_call, last_call_at)
                   OR last_back_at IS DISTINCT FROM COALESCE(:last_back, last_back_at)
                   OR loss_count IS DISTINCT FROM :loss_count)
        """),
            observations,
        )


async def serial_status_loop(engine: AsyncEngine, registry: Registry) -> None:
    while True:
        try:
            await asyncio.wait_for(sync_serial_status(engine, registry, now=time.time()), timeout=3)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("serial device status synchronization failed")
        await asyncio.sleep(1)
