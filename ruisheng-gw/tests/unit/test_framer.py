"""Length-aware framer — NOT CRC-boundary (RTU has no silent period over TCP).

Tests:
- 1/2/N 帧粘连切分正确
- DTU ASCII heartbeat 剥除（\r\n###HEARTBEAT\r\n 等）
- idle-timeout 300ms buffer flush resync
- 短帧不切（等更多字节）
"""

from __future__ import annotations

from ruisheng_gw.protocol.framer import Framer
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame


def _fc3_resp(slave: int, byte_count: int, data: bytes) -> bytes:
    body = bytes([slave, 0x03, byte_count]) + data
    return append_crc_to_frame(body)


def test_single_frame_feed() -> None:
    frame = _fc3_resp(1, 2, b"\x00\x0a")
    framer = Framer()
    framer.feed(frame)
    frames = list(framer.pop_frames())
    assert len(frames) == 1
    assert frames[0] == frame


def test_two_frames_concatenated() -> None:
    f1 = _fc3_resp(1, 2, b"\x00\x0a")
    f2 = _fc3_resp(2, 4, b"\x00\x14\x00\x1e")
    framer = Framer()
    framer.feed(f1 + f2)
    frames = list(framer.pop_frames())
    assert frames == [f1, f2]


def test_ascii_heartbeat_stripped() -> None:
    frame = _fc3_resp(1, 2, b"\x00\x0a")
    garbage = b"\r\n###HEARTBEAT\r\n"
    framer = Framer()
    framer.feed(garbage + frame + garbage)
    frames = list(framer.pop_frames())
    assert frames == [frame]
    assert framer.stats["heartbeat_stripped"] > 0


def test_modbus_frame_not_corrupted_by_regex() -> None:
    """Frames with byte values like 0x0A + 0x44 must NOT be stripped."""
    # FC3 response: slave=0x01 fc=0x03 bytecount=0x02 data=[0x0A, 0x44] + CRC
    # 0x0A = '\n', 0x44 = 'D' — would false-positive on old regex
    body = bytes([0x01, 0x03, 0x02, 0x0A, 0x44])
    frame = append_crc_to_frame(body)
    # Sanity: confirm the frame contains the dangerous bytes
    assert b"\x0a\x44" in frame
    framer = Framer()
    framer.feed(frame)
    frames = list(framer.pop_frames())
    assert frames == [frame]  # frame must not be corrupted


def test_incomplete_frame_waits() -> None:
    frame = _fc3_resp(1, 2, b"\x00\x0a")
    framer = Framer()
    framer.feed(frame[:4])  # partial
    assert list(framer.pop_frames()) == []
    framer.feed(frame[4:])
    assert list(framer.pop_frames()) == [frame]


def test_idle_timeout_resync() -> None:
    """buffer 超过 idle_ms 未 parse 成功 → drop buffer + metric."""
    framer = Framer(idle_ms=100)
    framer.feed(b"\x01\x99\x99\x99\x99")  # garbage that will never parse
    resync_count = framer.stats["resync"]
    framer.tick(now_ms=1000)  # >> 100ms later
    framer.tick(now_ms=1000 + 200)  # second tick to actually trigger
    assert framer.stats["resync"] > resync_count
    assert framer.buffer_len() == 0
