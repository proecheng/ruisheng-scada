"""gw health & metrics HTTP endpoints (aiohttp on :9090).

- /health   — liveness (进程存活 = 200)
- /ready    — readiness (DB, Redis, and latest batch flush are healthy)
- /metrics  — Prometheus text format
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from typing import Final

from aiohttp import web

from ruisheng_gw.management_auth import management_bearer_matches

OUTBOX_READINESS_FAILURE_THRESHOLD = 3


@dataclass
class HealthState:
    """全局 health 状态，由主 loop 更新。"""

    _db_ok: bool = False
    _redis_ok: bool = False
    _batch_ok: bool = True
    _outbox_pending: int = 0
    _outbox_relay_failures: int = 0
    _outbox_consecutive_failures: int = 0

    def set_db_ok(self, ok: bool) -> None:
        self._db_ok = ok

    def set_redis_ok(self, ok: bool) -> None:
        self._redis_ok = ok

    def mark_flush_ok(self) -> None:
        self._batch_ok = True

    def mark_flush_failed(self) -> None:
        self._batch_ok = False

    def is_ready(self) -> bool:
        return (
            self._db_ok
            and self._redis_ok
            and self._batch_ok
            and self._outbox_consecutive_failures < OUTBOX_READINESS_FAILURE_THRESHOLD
        )

    def internal_snapshot(self) -> dict[str, object]:
        return {
            "status": "ready" if self.is_ready() else "not_ready",
            "database": "ready" if self._db_ok else "failed",
            "redis": "ready" if self._redis_ok else "failed",
            "batch": "ready" if self._batch_ok else "failed",
            "outbox": (
                "ready"
                if self._outbox_consecutive_failures < OUTBOX_READINESS_FAILURE_THRESHOLD
                else "failed"
            ),
            "pid": os.getpid(),
            "observed_at": time.time(),
        }

    def set_outbox_pending(self, count: int) -> None:
        self._outbox_pending = count

    def mark_outbox_relay_failed(self) -> None:
        self._outbox_relay_failures += 1
        self._outbox_consecutive_failures += 1

    def mark_outbox_relay_ok(self) -> None:
        self._outbox_consecutive_failures = 0


HEALTH_STATE_KEY = web.AppKey("health_state", HealthState)
HEALTH_NETWORKS_KEY = web.AppKey("health_networks", tuple[IPv4Network | IPv6Network, ...])
HEALTH_TOKEN_DIGEST_KEY = web.AppKey("health_token_digest", str)
DEFAULT_HEALTH_CIDRS: Final = "127.0.0.1/32,::1/128"


def _parse_health_networks(value: str) -> tuple[IPv4Network | IPv6Network, ...]:
    networks: list[IPv4Network | IPv6Network] = []
    for raw_subject in value.split(","):
        subject = raw_subject.strip()
        if not subject:
            continue
        network = ip_network(subject, strict=False)
        if isinstance(network, IPv6Network) and network.network_address.ipv4_mapped is not None:
            raise ValueError("health source ACL must not contain IPv4-mapped IPv6 CIDRs")
        if network.prefixlen == 0:
            raise ValueError("health source ACL must not contain a default route")
        networks.append(network)
    if not networks:
        raise ValueError("health source ACL must contain at least one CIDR")
    return tuple(networks)


@web.middleware
async def _health_source_acl(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    peer = request.transport.get_extra_info("peername") if request.transport else None
    peer_host = peer[0] if isinstance(peer, tuple) and peer else None
    try:
        peer_ip = ip_address(str(peer_host))
    except ValueError:
        peer_ip = None
    if isinstance(peer_ip, IPv6Address) and peer_ip.ipv4_mapped is not None:
        peer_ip = IPv4Address(peer_ip.ipv4_mapped)
    networks = request.app[HEALTH_NETWORKS_KEY]
    if peer_ip is None or not any(peer_ip in network for network in networks):
        return web.json_response({"detail": "health source is not approved"}, status=403)
    response = await handler(request)
    return response


@web.middleware
async def _health_token_auth(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    expected_digest = request.app.get(HEALTH_TOKEN_DIGEST_KEY)
    if not management_bearer_matches(request.headers.get("Authorization"), expected_digest):
        return web.json_response({"detail": "management access denied"}, status=403)
    return await handler(request)


async def _health_handler(request: web.Request) -> web.Response:  # noqa: ARG001
    return web.json_response({"status": "alive"})


async def _ready_handler(request: web.Request) -> web.Response:
    state = request.app[HEALTH_STATE_KEY]
    if state.is_ready():
        return web.json_response({"ready": True})
    return web.json_response({"ready": False}, status=503)


async def _internal_ready_handler(request: web.Request) -> web.Response:
    state = request.app[HEALTH_STATE_KEY]
    snapshot = state.internal_snapshot()
    return web.json_response(snapshot, status=200 if snapshot["status"] == "ready" else 503)


async def _metrics_handler(request: web.Request) -> web.Response:
    state = request.app[HEALTH_STATE_KEY]
    body = (
        "# HELP ruisheng_gw_build_info Build info\n"
        "# TYPE ruisheng_gw_build_info gauge\n"
        'ruisheng_gw_build_info{version="0.1.0"} 1\n'
        "# HELP ruisheng_gw_alarm_outbox_pending Unpublished alarm outbox rows\n"
        "# TYPE ruisheng_gw_alarm_outbox_pending gauge\n"
        f"ruisheng_gw_alarm_outbox_pending {state._outbox_pending}\n"
        "# HELP ruisheng_gw_alarm_outbox_relay_failures_total Alarm outbox relay failures\n"
        "# TYPE ruisheng_gw_alarm_outbox_relay_failures_total counter\n"
        f"ruisheng_gw_alarm_outbox_relay_failures_total {state._outbox_relay_failures}\n"
        "# HELP ruisheng_gw_alarm_outbox_relay_consecutive_failures "
        "Consecutive alarm outbox relay failures\n"
        "# TYPE ruisheng_gw_alarm_outbox_relay_consecutive_failures gauge\n"
        f"ruisheng_gw_alarm_outbox_relay_consecutive_failures "
        f"{state._outbox_consecutive_failures}\n"
    )
    return web.Response(text=body, content_type="text/plain; version=0.0.4")


def create_health_app(
    state: HealthState,
    allowed_cidrs: str = DEFAULT_HEALTH_CIDRS,
    token_sha256: str | None = None,
) -> web.Application:
    app = web.Application(middlewares=(_health_source_acl, _health_token_auth))
    app[HEALTH_STATE_KEY] = state
    app[HEALTH_NETWORKS_KEY] = _parse_health_networks(allowed_cidrs)
    if token_sha256 is not None:
        app[HEALTH_TOKEN_DIGEST_KEY] = token_sha256
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ready", _ready_handler)
    app.router.add_get("/metrics", _metrics_handler)
    return app


def create_internal_health_app(state: HealthState) -> web.Application:
    app = web.Application()
    app[HEALTH_STATE_KEY] = state
    app.router.add_get("/ready", _internal_ready_handler)
    return app
