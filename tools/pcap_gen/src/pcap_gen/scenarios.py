"""伪设备场景生成器。输出 pcap + expected.json。"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from scapy.all import IP, TCP, Raw, wrpcap  # type: ignore[attr-defined]

from .modbus_frames import (
    encode_heartbeat,
    encode_read_holding,
    encode_read_holding_response,
    encode_register_frame,
)


def gen_normal_session(
    *,
    dev_ser: str,
    slave: int,
    frames_count: int,
    out_pcap: Path,
    out_expected: Path,
    seed: int = 42,
) -> None:
    """生成一条"注册 + N 次轮询"的正常会话 pcap。

    同步输出 expected.json：
      {
        "dev_ser": "...",
        "frames": [{"type": "register", ...}, {"type": "read", ...}, ...],
        "values": [[v0, v1], [v2, v3], ...],
      }
    """
    rng = random.Random(seed)
    packets = []
    expected_values: list[list[int]] = []
    client_ip = "10.0.0.1"
    server_ip = "10.0.0.2"
    sport = 10000 + rng.randint(0, 1000)
    dport = 6000

    # 注册帧
    reg = encode_register_frame(dev_ser_number=dev_ser)
    packets.append(
        IP(src=client_ip, dst=server_ip) / TCP(sport=sport, dport=dport, flags="PA") / Raw(load=reg)
    )

    # N 次：gw → 设备 read；设备 → gw response
    for _i in range(frames_count):
        req = encode_read_holding(slave=slave, start=0, count=2)
        packets.append(
            IP(src=server_ip, dst=client_ip)
            / TCP(sport=dport, dport=sport, flags="PA")
            / Raw(load=req)
        )
        values = [rng.randint(20, 80), rng.randint(100, 200)]
        resp = encode_read_holding_response(slave=slave, values=values)
        packets.append(
            IP(src=client_ip, dst=server_ip)
            / TCP(sport=sport, dport=dport, flags="PA")
            / Raw(load=resp)
        )
        expected_values.append(values)

    # 心跳
    hb = encode_heartbeat(slave=slave, token=rng.randint(0, 2**32 - 1))
    packets.append(
        IP(src=server_ip, dst=client_ip) / TCP(sport=dport, dport=sport, flags="PA") / Raw(load=hb)
    )

    wrpcap(str(out_pcap), packets)
    out_expected.write_text(
        json.dumps(
            {
                "scenario": "normal_session",
                "dev_ser": dev_ser,
                "slave": slave,
                "frames_count": frames_count,
                "values": expected_values,
                "generated_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
