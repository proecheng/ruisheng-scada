from __future__ import annotations

from ruisheng_gw.protocol.framer import Framer
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame


def test_framer_emits_fc21_register_frame() -> None:
    body = (
        bytes([0xFE, 0x15])
        + b"SN-001".ljust(24, b"\x00")
        + b"1.2".ljust(5, b"\x00")
        + b"3".ljust(3, b"\x00")
    )
    frame = append_crc_to_frame(body)
    framer = Framer()
    framer.feed(frame[:10])
    assert list(framer.pop_frames()) == []
    framer.feed(frame[10:])
    assert list(framer.pop_frames()) == [frame]
