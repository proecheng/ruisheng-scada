"""TCP server skeleton: bind + accept + graceful shutdown."""

from __future__ import annotations

import asyncio

from ruisheng_gw.transport.tcp_server import GwServer


async def _wait_accept(server: GwServer) -> None:
    for _ in range(50):
        if server.is_listening():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("server never listened")


async def test_server_accepts_connection_and_closes_on_shutdown() -> None:
    accepted: list[str] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        accepted.append(f"{peer[0]}:{peer[1]}")
        writer.close()
        await writer.wait_closed()

    server = GwServer(host="127.0.0.1", port=0, handler=handler)
    await server.start()
    await _wait_accept(server)
    port = server.actual_port()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.05)
    await server.shutdown()
    assert len(accepted) == 1


async def test_shutdown_is_idempotent() -> None:
    server = GwServer(host="127.0.0.1", port=0, handler=lambda r, w: None)  # type: ignore[arg-type]
    await server.start()
    await server.shutdown()
    await server.shutdown()  # second call no-op
