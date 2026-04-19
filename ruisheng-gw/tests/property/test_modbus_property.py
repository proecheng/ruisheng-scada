"""Hypothesis property test — CRC + framer round-trip on random bytes.

Runs 100 examples per CI (default Hypothesis settings); 1000 nightly.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from ruisheng_gw.protocol.framer import Framer
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame, verify_crc16


@given(st.binary(min_size=2, max_size=200))
def test_crc_roundtrip_any_body(body: bytes) -> None:
    frame = append_crc_to_frame(body)
    verify_crc16(frame)  # no raise


@given(
    slave=st.integers(min_value=1, max_value=247),
    byte_count=st.integers(min_value=2, max_value=60).filter(lambda n: n % 2 == 0),
    data_seed=st.binary(min_size=60, max_size=60),
)
@settings(max_examples=100)
def test_framer_extracts_fc3_resp(slave: int, byte_count: int, data_seed: bytes) -> None:
    data = data_seed[:byte_count]
    body = bytes([slave, 0x03, byte_count]) + data
    frame = append_crc_to_frame(body)

    framer = Framer()
    framer.feed(frame)
    frames = list(framer.pop_frames())
    assert frames == [frame]


@given(st.binary(min_size=4, max_size=200))
@settings(max_examples=100)
def test_framer_never_crashes_on_random_bytes(data: bytes) -> None:
    framer = Framer()
    framer.feed(data)
    # may or may not emit frames; must not raise
    list(framer.pop_frames())
