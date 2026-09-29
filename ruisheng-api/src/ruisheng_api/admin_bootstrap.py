"""Explicit maintenance-only first-administrator CLI; never called by server startup."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import BinaryIO, Literal
from uuid import UUID

from ruisheng_shared.models.logs import SoftLog
from ruisheng_shared.models.tenants import WxGroup
from ruisheng_shared.models.users import User
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import Config
from .core.security import hash_password, verify_password
from .core.tenant import apply_tenant_context

MAX_REQUEST_BYTES = 4096
OPERATION_UUID_VERSION = 4
AUDIT_EVENT = "first_administrator_created"
REQUEST_FIELDS = frozenset(
    {"schema_version", "action", "operation_id", "site_id", "user_name", "password"}
)


class BootstrapRejected(Exception):  # noqa: N818
    """Deliberately carries no database or input detail."""


@dataclass(frozen=True)
class BootstrapRequest:
    action: Literal["create", "status"]
    operation_id: str
    site_id: str
    user_name: str
    password: str = field(repr=False)

    def receipt(self, status: str) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "site_id": self.site_id,
            "user_name": self.user_name,
            "status": status,
        }

    def audit_context(self) -> dict[str, str | int]:
        return {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "site_id": self.site_id,
            "user_name": self.user_name,
            "authority": "Administrators",
            "control_authority": 0,
        }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapRejected
        result[key] = value
    return result


def read_request(stream: BinaryIO) -> BootstrapRequest:
    """Only stdin transports secrets. Bound before decoding and reject duplicate keys."""
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise BootstrapRejected
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or set(value) != REQUEST_FIELDS:
            raise BootstrapRejected
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise BootstrapRejected
        if any(not isinstance(value[key], str) for key in REQUEST_FIELDS - {"schema_version"}):
            raise BootstrapRejected
        if value["action"] not in {"create", "status"}:
            raise BootstrapRejected
        operation = UUID(value["operation_id"])
        if str(operation) != value["operation_id"] or operation.version != OPERATION_UUID_VERSION:
            raise BootstrapRejected
        if not re.fullmatch(r"site-[a-z0-9][a-z0-9-]{0,43}", value["site_id"]):
            raise BootstrapRejected
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{3,29}", value["user_name"]):
            raise BootstrapRejected
        if not re.fullmatch(r"[A-Za-z0-9_-]{32}", value["password"]):
            raise BootstrapRejected
        return BootstrapRequest(
            action=value["action"],
            operation_id=value["operation_id"],
            site_id=value["site_id"],
            user_name=value["user_name"],
            password=value["password"],
        )
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise BootstrapRejected from None


async def _assert_api_role(session: AsyncSession) -> None:
    role = (
        await session.execute(
            text(
                "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()
    if role != ("ruisheng_api", False, False):
        raise BootstrapRejected


async def bootstrap(
    factory: async_sessionmaker[AsyncSession], request: BootstrapRequest
) -> dict[str, str]:
    async with factory() as session, session.begin():
        if request.action == "status":
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        await session.execute(text("SET LOCAL search_path = public, pg_catalog"))
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        await session.execute(text("SET LOCAL statement_timeout = '10s'"))
        await _assert_api_role(session)
        await apply_tenant_context(session, usr_group=request.site_id, role="Administrators")
        if request.action == "create":
            # Fixed table order serializes first creation with all concurrent inserts.
            await session.execute(text("LOCK TABLE wx_groups IN SHARE ROW EXCLUSIVE MODE"))
            await session.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
        groups = int(await session.scalar(select(func.count()).select_from(WxGroup)) or 0)
        users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
        if request.action == "status":
            audits = (
                await session.scalars(
                    select(SoftLog)
                    .where(
                        SoftLog.msg == AUDIT_EVENT,
                        SoftLog.context["operation_id"].astext == request.operation_id,
                    )
                    .limit(2)
                )
            ).all()
            prior_audit = await session.scalar(
                select(SoftLog.id).where(SoftLog.msg == AUDIT_EVENT).limit(1)
            )
            if not groups and not users and prior_audit is None:
                return request.receipt("empty")
            user = await session.scalar(select(User).where(User.user_name == request.user_name))
            group = await session.get(WxGroup, request.site_id)
            if (
                groups == 1
                and users == 1
                and group is not None
                and user is not None
                and user.deleted_at is None
                and user.usr_group == request.site_id
                and user.authority == "Administrators"
                and user.control_authority == 0
                and len(audits) == 1
                and audits[0].level == "WARN"
                and audits[0].source == "api"
                and audits[0].context == request.audit_context()
                and verify_password(request.password, user.password_hash)
            ):
                return request.receipt("confirmed")
            return request.receipt("conflict")
        if groups or users:
            raise BootstrapRejected
        # An earlier operation whose accounts were removed must not be replayed.
        if await session.scalar(select(SoftLog.id).where(SoftLog.msg == AUDIT_EVENT).limit(1)):
            raise BootstrapRejected
        session.add(WxGroup(usr_group=request.site_id))
        await session.flush()
        session.add(
            User(
                user_name=request.user_name,
                password_hash=hash_password(request.password),
                authority="Administrators",
                control_authority=0,
                usr_group=request.site_id,
            )
        )
        session.add(
            SoftLog(
                id=func.nextval(func.pg_get_serial_sequence("soft_logs", "id")),
                level="WARN",
                source="api",
                msg=AUDIT_EVENT,
                context=request.audit_context(),
            )
        )
        await session.flush()
    return request.receipt("created")


async def run(request: BootstrapRequest) -> dict[str, str]:
    config = Config()
    url = make_url(config.db_url)
    if url.drivername != "postgresql+asyncpg" or url.username != "ruisheng_api":
        raise BootstrapRejected
    engine = create_async_engine(
        url, hide_parameters=True, echo=False, connect_args={"timeout": 5}, pool_pre_ping=True
    )
    try:
        return await bootstrap(async_sessionmaker(engine, expire_on_commit=False), request)
    finally:
        await engine.dispose()


def main() -> int:
    # A driver/SQL exception may contain parameters; this CLI never renders one.
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    request: BootstrapRequest | None = None
    try:
        if len(sys.argv) != 1:
            raise BootstrapRejected
        request = read_request(sys.stdin.buffer)
        receipt = asyncio.run(run(request))
        code = 0 if receipt["status"] in {"created", "confirmed", "empty"} else 2
    except BootstrapRejected:
        receipt = (
            request.receipt("rejected")
            if request
            else {"operation_id": "", "site_id": "", "user_name": "", "status": "rejected"}
        )
        code = 2
    except Exception:
        # Commit acknowledgement can be lost. Keep credentials and reconcile read-only.
        receipt = (
            request.receipt("unknown")
            if request
            else {"operation_id": "", "site_id": "", "user_name": "", "status": "unknown"}
        )
        code = 3
    finally:
        logging.disable(previous_logging_disable)
    sys.stdout.write(json.dumps(receipt, separators=(",", ":")) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
