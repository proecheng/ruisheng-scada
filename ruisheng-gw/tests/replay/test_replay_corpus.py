"""Replay Plan 0 pcap_gen corpus against live gw; assert DB/Redis state.

Corpus: tools/pcap_gen/corpus/generated/*.pcap
gw_server fixture: NOT YET IMPLEMENTED — requires full server wiring.
This test is skipped automatically when the corpus directory has no .pcap files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CORPUS_DIR = (
    Path(__file__).parent.parent.parent.parent / "tools" / "pcap_gen" / "corpus" / "generated"
)

_CORPUS_PCAPS = sorted(CORPUS_DIR.glob("*.pcap")) if CORPUS_DIR.exists() else []

pytestmark = pytest.mark.skipif(
    len(_CORPUS_PCAPS) == 0,
    reason="pcap corpus is empty — run tools/pcap_gen/scripts/gen_initial_corpus.py first",
)


@pytest.fixture(params=_CORPUS_PCAPS)
def corpus_case(request):
    pcap = request.param
    expected_json = pcap.with_suffix(".expected.json")
    return pcap, json.loads(expected_json.read_text())


async def test_replay_15_corpus(gw_server, corpus_case) -> None:
    """Replay pcap corpus preserving inter-frame timing.

    gw_server fixture is not yet implemented; this test is currently
    skipped because the corpus is empty. See Stage F plan for the
    fixture signature: gw_server.host, gw_server.port,
    gw_server.batch_writer.drain_and_flush(), gw_server.repo.
    """
    import asyncio

    from .pcap_reader import extract_tcp_payloads_with_timing

    pcap_path, expected = corpus_case
    payloads = extract_tcp_payloads_with_timing(pcap_path, port=5020)

    reader, writer = await asyncio.open_connection(gw_server.host, gw_server.port)
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

    await gw_server.batch_writer.drain_and_flush()

    repo = gw_server.repo
    rows = await repo.fetch_realtime(dev_number=expected["dev_number"])
    assert len(rows) == expected["frames_count"]
    for row, exp_val in zip(
        sorted(rows, key=lambda r: r.point_id), expected["values"], strict=False
    ):
        assert row.rt_value == pytest.approx(exp_val)
