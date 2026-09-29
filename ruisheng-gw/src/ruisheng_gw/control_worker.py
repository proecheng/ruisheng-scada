"""Consume audited, expiring DO commands; never replay a previously dispatched write."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ruisheng_gw.domain.registry import Registry
from ruisheng_gw.scheduler.do_control import PROFILE
from ruisheng_gw.transport.serial_bus import SerialBus

STREAM = "stream:control:cmd"
GROUP = "gw-control-consumer"
COMMAND_TTL_SEC = 15
INTERRUPTED_AFTER_SEC = 30
MAX_COMMAND_ID_LENGTH = 32
logger = logging.getLogger(__name__)


def reject_reason(action: dict[str, Any], age: float) -> str | None:  # noqa: PLR0911
    if action.get("dispatched_at"):
        return "previous_dispatch_not_replayed_output_unconfirmed"
    if action.get("kind") != "do_v1" or action.get("profile") != PROFILE:
        return "unsupported_control_command_not_sent"
    if not 0 <= age <= COMMAND_TTL_SEC:
        return "command_expired_not_sent"
    mask, value = action.get("mask"), action.get("value")
    if type(mask) is not int or mask not in (1, 2, 3):
        return "invalid_channel_selection"
    if type(value) is not int or value < 0 or value & ~mask:
        return "invalid_channel_value"
    if type(action.get("config_version")) is not int:
        return "missing_configuration_version"
    return None


class ControlWorker:
    def __init__(self, engine: AsyncEngine, redis: Any, registry: Registry, buses: list[SerialBus]):
        self.engine = engine
        self.redis = redis
        self.registry = registry
        self.buses = buses
        self.consumer = "gw-" + uuid.uuid4().hex

    async def finish(self, dev: str, cmd: str, tenant: str, result: dict[str, object]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text("""UPDATE user_control_actions SET result=:status,
                completed_at=now(), action=jsonb_set(action,'{execution}',CAST(:detail AS jsonb))
                WHERE dev_number=:d AND cmd_id=:c AND usr_group=:g AND result='pending'"""),
                {
                    "d": dev,
                    "c": cmd,
                    "g": tenant,
                    "status": result["status"],
                    "detail": json.dumps(result),
                },
            )

    async def handle(self, fields: dict[str, str]) -> None:
        cmd = fields.get("cmd_id", "")
        try:
            payload = json.loads(fields.get("payload", "{}"))
        except (TypeError, ValueError):
            return
        dev = payload.get("dev_number") if isinstance(payload, dict) else None
        if (
            not isinstance(dev, str)
            or not dev
            or not isinstance(cmd, str)
            or not cmd
            or len(cmd) > MAX_COMMAND_ID_LENGTH
        ):
            return
        async with self.engine.begin() as conn:
            await conn.execute(text("SET LOCAL statement_timeout='5s'"))
            row = (
                (
                    await conn.execute(
                        text("""SELECT action,acted_at,result,usr_group
                FROM user_control_actions WHERE dev_number=:d AND cmd_id=:c FOR UPDATE"""),
                        {"d": dev, "c": cmd},
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["result"] != "pending":
                return
            action = dict(row["action"])
            tenant = row["usr_group"]  # Authoritative audit tenant, never a Redis field.
            age = (datetime.now(UTC) - row["acted_at"]).total_seconds()
            if action.get("dispatched_at") and age < INTERRUPTED_AFTER_SEC:
                return  # Another delivery cannot change an in-flight operation's result.
            reason = reject_reason(action, age)
            if not reason:
                # Commit before any physical write. A crash after this point is never replayed.
                await conn.execute(
                    text("""UPDATE user_control_actions SET action=
                    jsonb_set(action,'{dispatched_at}',to_jsonb(now()))
                    WHERE dev_number=:d AND cmd_id=:c AND usr_group=:g AND result='pending'"""),
                    {"d": dev, "c": cmd, "g": tenant},
                )
        if reason:
            await self.finish(dev, cmd, tenant, {"status": "failed", "reason": reason})
            return
        result: dict[str, object]
        try:
            async with asyncio.timeout(COMMAND_TTL_SEC):
                result = await self.execute(dev, tenant, action)
        except TimeoutError:
            result = {"status": "timeout", "reason": "execution_timeout_output_unconfirmed"}
        except Exception:
            logger.exception("DO execution failed device=%s cmd=%s", dev, cmd)
            result = {"status": "failed", "reason": "execution_failed_output_unconfirmed"}
        await self.finish(dev, cmd, tenant, result)

    async def execute(self, dev: str, tenant: str, action: dict[str, Any]) -> dict[str, object]:
        async with self.engine.begin() as conn:
            await conn.execute(text("SET LOCAL statement_timeout='5s'"))
            # Config edits take the device's update lock. Hold a shared lock until this
            # bounded operation ends so addresses/profile cannot change between writes.
            device = (
                (
                    await conn.execute(
                        text("""SELECT update_flag,is_enabled,transport_type,
                serial_port,modbus_addr,read_profile FROM devices WHERE dev_number=:d
                AND usr_group=:g AND deleted_at IS NULL FOR SHARE"""),
                        {"d": dev, "g": tenant},
                    )
                )
                .mappings()
                .first()
            )
            profile = (
                await conn.execute(
                    text("""SELECT base_msg_value FROM device_static_data
                WHERE dev_number=:d AND base_msg_name='do_control_profile'
                ORDER BY id DESC LIMIT 1 FOR SHARE"""),
                    {"d": dev},
                )
            ).scalar_one_or_none()
            entry = self.registry.get(dev)
            if (
                device is None
                or entry is None
                or not device["is_enabled"]
                or device["transport_type"] != "serial"
                or profile != PROFILE
                or device["read_profile"] != "zero_origin_38"
                or device["update_flag"] != action["config_version"]
                or entry.config_version != device["update_flag"]
                or entry.serial_port != device["serial_port"]
                or entry.modbus_addr != device["modbus_addr"]
                or entry.device.usr_group != tenant
            ):
                return {"status": "failed", "reason": "device_configuration_changed_not_sent"}
            for bus in self.buses:
                if bus._port == entry.serial_port and bus.poller is not None:
                    remaining = (
                        datetime.fromisoformat(action["expires_at"]) - datetime.now(UTC)
                    ).total_seconds()
                    return await bus.poller.do_control.submit(
                        entry, action["mask"], action["value"], remaining
                    )
            return {"status": "failed", "reason": "serial_unavailable_not_sent"}

    async def expire(self) -> None:
        # A queue publication failure or interrupted worker must not leave the UI pending forever.
        async with self.engine.begin() as conn:
            await conn.execute(text("SET LOCAL statement_timeout='5s'"))
            expired = (
                await conn.execute(
                    text("""SELECT dev_number,cmd_id,usr_group
                FROM user_control_actions WHERE result='pending' AND action->>'kind'='do_v1'
                AND acted_at<now()-interval '30 seconds' ORDER BY acted_at LIMIT 100""")
                )
            ).mappings()
            for command in expired:
                await conn.execute(
                    text("""UPDATE user_control_actions SET result='timeout',
                    completed_at=now(), action=jsonb_set(action,'{execution}',
                    '{"status":"timeout","reason":"expired_or_interrupted_output_unconfirmed"}'::jsonb)
                    WHERE dev_number=:d AND cmd_id=:c AND usr_group=:g
                    AND result='pending' AND action->>'kind'='do_v1'
                    AND acted_at<now()-interval '30 seconds'"""),
                    {"d": command["dev_number"], "c": command["cmd_id"], "g": command["usr_group"]},
                )

    async def run(self) -> None:
        while True:
            try:
                try:
                    await self.redis.xgroup_create(STREAM, GROUP, id="0-0", mkstream=True)
                except ResponseError as exc:
                    if "BUSYGROUP" not in str(exc):
                        raise
                while True:
                    await self.expire()
                    claimed = await self.redis.xautoclaim(
                        STREAM, GROUP, self.consumer, 30000, "0-0", count=1
                    )
                    messages = claimed[1]
                    if not messages:
                        batches = await self.redis.xreadgroup(
                            GROUP, self.consumer, {STREAM: ">"}, count=1, block=1000
                        )
                        messages = batches[0][1] if batches else []
                    for message_id, fields in messages:
                        await self.handle(fields)
                        await self.redis.xack(STREAM, GROUP, message_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("control consumer iteration failed")
                await asyncio.sleep(2)
