"""Replay generated device sessions against a live gateway."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import ClientSession

CORPUS_DIR = (
    Path(__file__).parent.parent.parent.parent / "tools" / "pcap_gen" / "corpus" / "generated"
)

_CORPUS_PCAPS = sorted(CORPUS_DIR.glob("*.pcap")) if CORPUS_DIR.exists() else []

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        len(_CORPUS_PCAPS) == 0,
        reason="pcap corpus is empty — run tools/pcap_gen/scripts/gen_initial_corpus.py first",
    ),
]


@pytest.fixture(params=_CORPUS_PCAPS)
def corpus_case(request):
    pcap = request.param
    expected_json = pcap.with_suffix(".expected.json")
    return pcap, json.loads(expected_json.read_text())


async def test_replay_15_corpus(gw_server, corpus_case) -> None:
    """Replay device-to-gateway frames and verify persisted values."""
    import asyncio

    from .pcap_reader import extract_tcp_payloads_with_timing

    async with (
        ClientSession() as session,
        session.get(f"http://{gw_server.host}:{gw_server.health_port}/ready") as response,
    ):
        assert response.status == 200

    pcap_path, expected = corpus_case
    payloads = extract_tcp_payloads_with_timing(pcap_path, port=5020)
    assert len(payloads) == expected["frames_count"] + 1

    _reader, writer = await asyncio.open_connection(gw_server.host, gw_server.port)
    prev_ts: float | None = None
    for p in payloads:
        if prev_ts is not None:
            delta = (p.ts - prev_ts) / 100  # fast_mode: /100
            if delta > 0:
                await asyncio.sleep(delta)
        writer.write(p.data)
        await writer.drain()
        prev_ts = p.ts
    writer.close()
    await writer.wait_closed()

    dev_number = expected["dev_ser"]
    final_values = expected["values"][-1]
    rows = await gw_server.wait_for_realtime(
        dev_number=dev_number,
        expected_values=final_values,
    )
    assert [row.rt_value for row in rows] == pytest.approx(final_values)
    assert await gw_server.history_count(dev_number) == expected["frames_count"] * 2
