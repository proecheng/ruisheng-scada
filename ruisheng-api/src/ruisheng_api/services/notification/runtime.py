from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import math
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...config import Config
from ...core.tenant import apply_tenant_context
from .base import AlarmNotification, ProviderResult
from .email import EmailNotifier
from .sms import CustomHttpSmsNotifier
from .voice import CustomHttpVoiceNotifier
from .wechat import WechatNotifier

MAX_EVENT_BYTES = 8_192
MAX_FUTURE_SKEW_SEC = 300
MAX_EVENT_AGE_SEC = 7 * 86_400


class AlarmStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    event_id: int = Field(gt=0)
    triggered_at: datetime
    alarm_cfg_id: int = Field(gt=0)
    dev_number: str = Field(min_length=1, max_length=50)
    point_id: int = Field(gt=0)
    value: float
    limit: float

    @field_validator("value", "limit")
    @classmethod
    def finite_number(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("number must be finite")
        return value

    @field_validator("triggered_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("triggered_at must include timezone")
        return value


class MaterializationError(ValueError):
    pass


@dataclass(frozen=True)
class ClaimedDelivery:
    id: int
    usr_group: str
    lease_version: int


@dataclass(frozen=True)
class MaterializationResult:
    dispatch_id: int
    usr_group: str
    alarm_name: str
    created: bool = True


@dataclass(frozen=True)
class ContactTarget:
    ref: str
    value: str


@dataclass(frozen=True)
class DeliveryOutcome:
    status: Literal["sent", "retry", "failed", "skipped"]
    error_class: str | None = None
    http_status: int | None = None
    retry_after_sec: int | None = None


class NotificationMetrics:
    def __init__(self) -> None:
        self.materialize_failures = 0
        self.stale_completions = 0
        self.failures: dict[str, int] = {}
        self.pending = 0
        self.oldest_age_sec = 0.0

    def record_failure(self, error_class: str) -> None:
        self.failures[error_class] = self.failures.get(error_class, 0) + 1


def parse_alarm_event(fields: dict[str, Any]) -> AlarmStreamEvent:
    size = sum(len(str(key)) + len(str(value)) for key, value in fields.items())
    if size > MAX_EVENT_BYTES:
        raise MaterializationError("event_too_large")
    try:
        normalized = dict(fields)
        normalized["schema_version"] = int(normalized.get("schema_version", 0))
        return AlarmStreamEvent.model_validate(normalized)
    except (TypeError, ValueError, ValidationError) as exc:
        raise MaterializationError("invalid_event") from exc


def contact_fingerprint(secret: str, contact: str) -> str:
    return hmac.new(secret.encode(), contact.encode(), hashlib.sha256).hexdigest()


async def _db_now(session: AsyncSession) -> datetime:
    return cast(datetime, (await session.execute(text("SELECT clock_timestamp()"))).scalar_one())


async def materialize_event(
    session_factory: async_sessionmaker[AsyncSession],
    event: AlarmStreamEvent,
    *,
    fingerprint_secret: str,
    provider_enabled: dict[str, bool],
    max_event_age_sec: int = MAX_EVENT_AGE_SEC,
) -> MaterializationResult:
    async with session_factory() as session, session.begin():
        await apply_tenant_context(session, usr_group="", role="Administrators")
        now = await _db_now(session)
        age = (now - event.triggered_at).total_seconds()
        if age < -MAX_FUTURE_SKEW_SEC or age > max_event_age_sec:
            raise MaterializationError("event_time_out_of_range")
        alarm = (
            (
                await session.execute(
                    text(
                        "SELECT id, triggered_at, alarm_cfg_id, dev_number, point_id, "
                        "       alarm_name, alarm_msg, alarm_value, limit_value, usr_group "
                        "FROM alarm_records WHERE id = :id AND triggered_at = :ts "
                        "AND alarm_cfg_id = :cfg"
                    ),
                    {"id": event.event_id, "ts": event.triggered_at, "cfg": event.alarm_cfg_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if alarm is None:
            raise MaterializationError("alarm_identity_not_found")
        if (
            alarm["dev_number"] != event.dev_number
            or alarm["point_id"] != event.point_id
            or float(alarm["alarm_value"]) != event.value
            or float(alarm["limit_value"]) != event.limit
        ):
            raise MaterializationError("alarm_identity_mismatch")
        tenant = str(alarm["usr_group"])
        await apply_tenant_context(session, usr_group=tenant, role="Company")
        subscriptions = (
            (
                await session.execute(
                    text(
                        "SELECT s.user_name, s.channel FROM alarm_notification_subscriptions s "
                        "WHERE s.alarm_cfg_id = :cfg AND s.usr_group = :tenant "
                        "AND s.created_at <= :triggered "
                        "AND (s.deleted_at IS NULL OR s.deleted_at > :triggered) ORDER BY s.id"
                    ),
                    {
                        "cfg": event.alarm_cfg_id,
                        "tenant": tenant,
                        "triggered": event.triggered_at,
                    },
                )
            )
            .mappings()
            .all()
        )
        status = "materialized" if subscriptions else "no_subscription"
        dispatch_id = (
            await session.execute(
                text(
                    "INSERT INTO notification_dispatches "
                    "(alarm_id, alarm_triggered_at, alarm_cfg_id, usr_group, trace_id, status) "
                    "VALUES (:alarm, :ts, :cfg, :tenant, :trace, :status) "
                    "ON CONFLICT (alarm_id, alarm_triggered_at, alarm_cfg_id) DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "alarm": event.event_id,
                    "ts": event.triggered_at,
                    "cfg": event.alarm_cfg_id,
                    "tenant": tenant,
                    "trace": f"alarm-{event.event_id}-{int(event.triggered_at.timestamp())}",
                    "status": status,
                },
            )
        ).scalar_one_or_none()
        if dispatch_id is None:
            dispatch_id = (
                await session.execute(
                    text(
                        "SELECT id FROM notification_dispatches WHERE alarm_id = :id "
                        "AND alarm_triggered_at = :ts AND alarm_cfg_id = :cfg"
                    ),
                    {"id": event.event_id, "ts": event.triggered_at, "cfg": event.alarm_cfg_id},
                )
            ).scalar_one()
            return MaterializationResult(
                int(dispatch_id), tenant, str(alarm["alarm_name"] or "alarm"), created=False
            )
        for subscription in subscriptions:
            channel = str(subscription["channel"])
            user_name = str(subscription["user_name"])
            contacts = await _load_contacts(session, tenant, user_name, channel)
            if not contacts:
                contacts = [ContactTarget(ref="missing", value="")]
            for contact in contacts:
                skipped = not provider_enabled.get(channel, False) or not contact.value
                error_class = (
                    "provider_disabled"
                    if not provider_enabled.get(channel, False)
                    else "contact_missing"
                    if skipped
                    else None
                )
                await session.execute(
                    text(
                        "INSERT INTO notification_deliveries "
                        "(dispatch_id, usr_group, user_name, channel, contact_ref, "
                        " contact_fingerprint, "
                        " status, last_error_class) "
                        "VALUES (:dispatch, :tenant, :user, :channel, :contact_ref, :fingerprint, "
                        " :status, :error) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "dispatch": dispatch_id,
                        "tenant": tenant,
                        "user": user_name,
                        "channel": channel,
                        "contact_ref": contact.ref,
                        "fingerprint": contact_fingerprint(fingerprint_secret, contact.value),
                        "status": "skipped" if skipped else "pending",
                        "error": error_class,
                    },
                )
        await _update_channels_projection(session, event.event_id, event.triggered_at)
        return MaterializationResult(int(dispatch_id), tenant, str(alarm["alarm_name"] or "alarm"))


async def _load_contacts(
    session: AsyncSession, tenant: str, user_name: str, channel: str
) -> list[ContactTarget]:
    if channel == "wechat":
        sql = (
            "SELECT concat('wechat:', b.contact_id) AS contact_ref, b.openid AS contact "
            "FROM user_wx_bindings b "
            "JOIN users u ON u.user_name = b.user_name AND u.usr_group = b.usr_group "
            "WHERE b.user_name = :user AND b.usr_group = :tenant AND u.deleted_at IS NULL"
        )
    elif channel == "email":
        sql = (
            "SELECT concat('email:', e.id) AS contact_ref, e.email AS contact "
            "FROM user_emails e JOIN users u ON u.user_name = e.user_name "
            "WHERE e.user_name = :user AND u.usr_group = :tenant AND u.deleted_at IS NULL"
        )
    else:
        sql = (
            "SELECT concat('phone:', p.id) AS contact_ref, p.phone_number AS contact "
            "FROM user_phone_numbers p "
            "JOIN users u ON u.user_name = p.user_name "
            "WHERE p.user_name = :user AND u.usr_group = :tenant AND u.deleted_at IS NULL"
        )
    rows = await session.execute(text(sql), {"user": user_name, "tenant": tenant})
    return [ContactTarget(ref=str(row.contact_ref), value=str(row.contact)) for row in rows]


async def claim_deliveries(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    limit: int,
    lease_sec: int,
) -> list[ClaimedDelivery]:
    async with session_factory() as session, session.begin():
        await apply_tenant_context(session, usr_group="", role="Administrators")
        rows = (
            (
                await session.execute(
                    text(
                        "WITH candidates AS ("
                        " SELECT id FROM notification_deliveries "
                        " WHERE ((status IN ('pending','retry') AND next_attempt_at <= clock_timestamp()) "
                        "    OR (status = 'leased' AND leased_until < clock_timestamp())) "
                        " ORDER BY next_attempt_at, id FOR UPDATE SKIP LOCKED LIMIT :limit"
                        ") UPDATE notification_deliveries d SET status = 'leased', "
                        " lease_owner = :worker, "
                        " leased_until = clock_timestamp() + make_interval(secs => :lease), "
                        " lease_version = lease_version + 1, updated_at = clock_timestamp() "
                        "FROM candidates c WHERE d.id = c.id "
                        "RETURNING d.id, d.usr_group, d.lease_version"
                    ),
                    {"worker": worker_id, "lease": lease_sec, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return [ClaimedDelivery(**dict(row)) for row in rows]


async def _resolve_delivery(
    session: AsyncSession,
    claimed: ClaimedDelivery,
    *,
    worker_id: str,
) -> tuple[dict[str, Any], str | None]:
    await apply_tenant_context(session, usr_group=claimed.usr_group, role="Company")
    row = (
        (
            await session.execute(
                text(
                    "SELECT d.id, d.user_name, d.channel, d.contact_ref, "
                    "       d.contact_fingerprint, d.attempt_count, "
                    "       p.trace_id, p.alarm_id, a.dev_number, a.alarm_name, a.alarm_msg, "
                    "       a.alarm_value, a.limit_value "
                    "FROM notification_deliveries d "
                    "JOIN notification_dispatches p ON p.id = d.dispatch_id "
                    "JOIN alarm_records a ON a.id = p.alarm_id "
                    "AND a.triggered_at = p.alarm_triggered_at "
                    "AND a.alarm_cfg_id = p.alarm_cfg_id "
                    "WHERE d.id = :id AND d.lease_version = :version AND d.status = 'leased' "
                    "AND d.lease_owner = :worker AND d.leased_until >= clock_timestamp()"
                ),
                {
                    "id": claimed.id,
                    "version": claimed.lease_version,
                    "worker": worker_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return {}, None
    contacts = await _load_contacts(
        session, claimed.usr_group, str(row["user_name"]), str(row["channel"])
    )
    contact = next(
        (target.value for target in contacts if target.ref == row["contact_ref"]),
        None,
    )
    return dict(row), contact


async def renew_delivery_lease(
    session_factory: async_sessionmaker[AsyncSession],
    claimed: ClaimedDelivery,
    *,
    worker_id: str,
    lease_sec: int,
) -> datetime | None:
    """Renew an unexpired owned lease immediately before provider I/O."""
    async with session_factory() as session, session.begin():
        await apply_tenant_context(session, usr_group=claimed.usr_group, role="Company")
        return cast(
            datetime | None,
            (
                await session.execute(
                    text(
                        "UPDATE notification_deliveries "
                        "SET leased_until = clock_timestamp() + make_interval(secs => :lease), "
                        "    updated_at = clock_timestamp() "
                        "WHERE id = :id AND status = 'leased' AND lease_owner = :worker "
                        "AND lease_version = :version AND leased_until >= clock_timestamp() "
                        "RETURNING clock_timestamp()"
                    ),
                    {
                        "lease": lease_sec,
                        "id": claimed.id,
                        "worker": worker_id,
                        "version": claimed.lease_version,
                    },
                )
            ).scalar_one_or_none(),
        )


async def process_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    claimed: ClaimedDelivery,
    *,
    cfg: Config,
    worker_id: str,
    metrics: NotificationMetrics,
) -> None:
    outcome: DeliveryOutcome | None
    started_at: datetime | None = None
    async with session_factory() as session, session.begin():
        row, contact = await _resolve_delivery(session, claimed, worker_id=worker_id)
        if not row:
            metrics.stale_completions += 1
            return
        if contact is None:
            outcome = DeliveryOutcome("skipped", "contact_missing")
            provider = None
        else:
            provider = await _build_provider(session, claimed.usr_group, str(row["channel"]), cfg)
            outcome = DeliveryOutcome("skipped", "provider_disabled") if provider is None else None
        notification = AlarmNotification(
            trace_id=str(row["trace_id"]),
            event_id=int(row["alarm_id"]),
            dev_number=str(row["dev_number"]),
            alarm_name=str(row["alarm_name"] or "alarm"),
            value=float(row["alarm_value"]),
            limit=float(row["limit_value"]),
            user_name=str(row["user_name"]),
            contact=contact or "",
            msg=str(row["alarm_msg"] or ""),
        )
        started_at = await _db_now(session)
    if outcome is None and provider is not None:
        started_at = await renew_delivery_lease(
            session_factory,
            claimed,
            worker_id=worker_id,
            lease_sec=cfg.notification_lease_sec,
        )
        if started_at is None:
            metrics.stale_completions += 1
            return
        try:
            provider_result = await asyncio.wait_for(
                _send_provider(provider, notification),
                timeout=cfg.notification_provider_timeout_sec,
            )
            outcome = DeliveryOutcome(
                "sent"
                if provider_result.sent
                else "retry"
                if provider_result.retryable
                else "failed",
                provider_result.error_class,
                provider_result.http_status,
                provider_result.retry_after_sec,
            )
        except (TimeoutError, OSError):
            outcome = DeliveryOutcome("retry", "transport")
        except Exception:
            outcome = DeliveryOutcome("retry", "provider_exception")
    assert outcome is not None
    await finalize_delivery(
        session_factory,
        claimed,
        outcome,
        worker_id=worker_id,
        max_attempts=cfg.notification_max_attempts,
        metrics=metrics,
        started_at=started_at,
    )


async def _send_provider(provider: Any, notification: AlarmNotification) -> ProviderResult:
    send_outcome = getattr(provider, "send_outcome", None)
    if send_outcome is not None:
        return cast(ProviderResult, await send_outcome(notification))
    sent = await provider.send(notification)
    return ProviderResult(sent=bool(sent), error_class=None if sent else "rejected")


async def _build_provider(
    session: AsyncSession, tenant: str, channel: str, cfg: Config
) -> Any | None:
    if channel == "wechat" and cfg.notification_wechat_enabled:
        group = (
            (
                await session.execute(
                    text("SELECT token, template_id FROM wx_groups WHERE usr_group = :tenant"),
                    {"tenant": tenant},
                )
            )
            .mappings()
            .one_or_none()
        )
        if group and group["token"] and group["template_id"]:
            return WechatNotifier(
                access_token=str(group["token"]), template_id=str(group["template_id"])
            )
    if channel == "email" and cfg.notification_email_enabled:
        return EmailNotifier(
            host=cfg.notification_email_host,
            port=cfg.notification_email_port,
            user=cfg.notification_email_user,
            password=cfg.notification_email_password,
            tls=cfg.notification_email_tls,
        )
    if channel == "sms_custom_http" and cfg.notification_sms_enabled:
        return CustomHttpSmsNotifier(
            endpoint=cfg.notification_sms_endpoint,
            api_key=cfg.notification_sms_api_key,
        )
    if channel == "voice_custom_http" and cfg.notification_voice_enabled:
        return CustomHttpVoiceNotifier(
            endpoint=cfg.notification_voice_endpoint,
            api_key=cfg.notification_voice_api_key,
        )
    return None


async def finalize_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    claimed: ClaimedDelivery,
    outcome: DeliveryOutcome,
    *,
    worker_id: str,
    max_attempts: int,
    metrics: NotificationMetrics,
    started_at: datetime | None = None,
) -> None:
    async with session_factory() as session, session.begin():
        await apply_tenant_context(session, usr_group=claimed.usr_group, role="Company")
        current_attempt = (
            await session.execute(
                text(
                    "SELECT attempt_count FROM notification_deliveries "
                    "WHERE id = :id AND status = 'leased' AND lease_owner = :worker "
                    "AND lease_version = :version AND leased_until >= clock_timestamp()"
                ),
                {
                    "id": claimed.id,
                    "worker": worker_id,
                    "version": claimed.lease_version,
                },
            )
        ).scalar_one_or_none()
        if current_attempt is None:
            metrics.stale_completions += 1
            return
        attempt_no = int(current_attempt) + 1
        status = outcome.status
        if status == "retry" and attempt_no >= max_attempts:
            status = "failed"
        delay = min(3600, 2 ** min(attempt_no, 10) * 5)
        if outcome.retry_after_sec is not None:
            delay = min(3600, max(delay, outcome.retry_after_sec))
        updated = await session.execute(
            text(
                "UPDATE notification_deliveries SET status = CAST(:status AS varchar), "
                "attempt_count = :attempt, lease_owner = NULL, leased_until = NULL, "
                "last_error_class = :error, "
                "next_attempt_at = CASE WHEN CAST(:status AS varchar) = 'retry' "
                "THEN clock_timestamp() + make_interval(secs => :delay) ELSE next_attempt_at END, "
                "sent_at = CASE WHEN CAST(:status AS varchar) = 'sent' "
                "THEN clock_timestamp() ELSE sent_at END, "
                "updated_at = clock_timestamp() "
                "WHERE id = :id AND status = 'leased' AND lease_owner = :worker "
                "AND lease_version = :version AND leased_until >= clock_timestamp()"
            ),
            {
                "status": status,
                "attempt": attempt_no,
                "error": outcome.error_class,
                "delay": delay,
                "id": claimed.id,
                "worker": worker_id,
                "version": claimed.lease_version,
            },
        )
        if getattr(updated, "rowcount", 0) != 1:
            metrics.stale_completions += 1
            return
        await session.execute(
            text(
                "INSERT INTO notification_delivery_attempts "
                "(delivery_id, usr_group, attempt_no, worker_id, outcome, error_class, "
                " http_status, retry_after_sec, detail, started_at, finished_at) "
                "VALUES (:delivery, :tenant, :attempt, :worker, :outcome, :error, "
                " :http, :retry, :detail, COALESCE(:started, clock_timestamp()), "
                " clock_timestamp())"
            ),
            {
                "delivery": claimed.id,
                "tenant": claimed.usr_group,
                "attempt": attempt_no,
                "worker": worker_id,
                "outcome": status,
                "error": outcome.error_class,
                "http": outcome.http_status,
                "retry": outcome.retry_after_sec,
                "detail": outcome.error_class,
                "started": started_at,
            },
        )
        alarm = (
            await session.execute(
                text(
                    "SELECT p.alarm_id, p.alarm_triggered_at "
                    "FROM notification_deliveries d "
                    "JOIN notification_dispatches p ON p.id = d.dispatch_id WHERE d.id = :id"
                ),
                {"id": claimed.id},
            )
        ).one()
        await _update_channels_projection(session, alarm.alarm_id, alarm.alarm_triggered_at)
        if outcome.error_class:
            metrics.record_failure(outcome.error_class)


async def _update_channels_projection(
    session: AsyncSession, alarm_id: int, triggered_at: datetime
) -> None:
    locked = (
        await session.execute(
            text("SELECT 1 FROM alarm_records WHERE id = :id AND triggered_at = :ts FOR UPDATE"),
            {"id": alarm_id, "ts": triggered_at},
        )
    ).scalar_one_or_none()
    if locked is None:
        return
    rows = (
        (
            await session.execute(
                text(
                    "SELECT d.channel, d.status, count(*) AS n FROM notification_deliveries d "
                    "JOIN notification_dispatches p ON p.id = d.dispatch_id "
                    "WHERE p.alarm_id = :id AND p.alarm_triggered_at = :ts "
                    "GROUP BY d.channel, d.status"
                ),
                {"id": alarm_id, "ts": triggered_at},
            )
        )
        .mappings()
        .all()
    )
    projection: dict[str, dict[str, int]] = {}
    for row in rows:
        visible = str(row["status"])
        if visible in {"retry", "leased"}:
            visible = "pending"
        projection.setdefault(
            str(row["channel"]),
            dict.fromkeys(("sent", "failed", "skipped", "pending"), 0),
        )[visible] += int(row["n"])
    await session.execute(
        text(
            "UPDATE alarm_records SET channels_sent = CAST(:projection AS JSONB) "
            "WHERE id = :id AND triggered_at = :ts"
        ),
        {"projection": __import__("json").dumps(projection), "id": alarm_id, "ts": triggered_at},
    )


async def cleanup_notification_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session, session.begin():
        await apply_tenant_context(session, usr_group="", role="Administrators")
        result = await session.execute(text("SELECT cleanup_notification_audit_rows()"))
        return int(result.scalar_one())


async def refresh_notification_metrics(
    session_factory: async_sessionmaker[AsyncSession], metrics: NotificationMetrics
) -> None:
    async with session_factory() as session, session.begin():
        await apply_tenant_context(session, usr_group="", role="Administrators")
        row = (
            await session.execute(
                text(
                    "SELECT count(*) AS pending, COALESCE(EXTRACT(EPOCH FROM "
                    "(clock_timestamp() - min(created_at))), 0) AS oldest "
                    "FROM notification_deliveries WHERE status IN ('pending','retry','leased')"
                )
            )
        ).one()
        metrics.pending = int(row.pending)
        metrics.oldest_age_sec = float(row.oldest)


async def delivery_worker_loop(
    session_factory: async_sessionmaker[AsyncSession],
    cfg: Config,
    stop_event: asyncio.Event,
    metrics: NotificationMetrics,
) -> None:
    worker_id = f"api-notify-{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    while not stop_event.is_set():
        try:
            claimed = await claim_deliveries(
                session_factory,
                worker_id=worker_id,
                limit=min(cfg.notification_worker_batch, cfg.notification_worker_concurrency),
                lease_sec=cfg.notification_lease_sec,
            )
            if claimed:
                semaphore = asyncio.Semaphore(cfg.notification_worker_concurrency)
                await asyncio.gather(
                    *(
                        _process_claimed(
                            session_factory,
                            item,
                            cfg,
                            worker_id,
                            metrics,
                            semaphore,
                        )
                        for item in claimed
                    )
                )
            await refresh_notification_metrics(session_factory, metrics)
        except Exception:
            logger.exception("notification delivery worker iteration failed")
            claimed = []
        if not claimed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=1)


async def _process_claimed(
    session_factory: async_sessionmaker[AsyncSession],
    item: ClaimedDelivery,
    cfg: Config,
    worker_id: str,
    metrics: NotificationMetrics,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        try:
            await process_delivery(
                session_factory,
                item,
                cfg=cfg,
                worker_id=worker_id,
                metrics=metrics,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("notification delivery failed delivery_id={}", item.id)
            metrics.record_failure("worker_exception")
