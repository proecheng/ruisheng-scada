"""Read TCP payloads with timing from Plan 0 pcap_gen corpus."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class PcapPayload(NamedTuple):
    ts: float
    data: bytes


def extract_tcp_payloads_with_timing(pcap_path: Path, *, port: int = 5020) -> list[PcapPayload]:
    from scapy.all import TCP, Raw, rdpcap  # type: ignore[attr-defined]

    out: list[PcapPayload] = []
    for pkt in rdpcap(str(pcap_path)):
        if TCP not in pkt:
            continue
        tcp = pkt[TCP]
        if port not in (tcp.dport, tcp.sport):
            continue
        if Raw not in pkt:
            continue
        out.append(PcapPayload(ts=float(pkt.time), data=bytes(pkt[Raw].load)))
    return out
