"""Length-aware ModBus RTU-on-TCP framer.

Strategy:
1. Strip ASCII garbage runs ([\\r\\n][A-Z#@!][\\w=:.,]+) BEFORE framing
2. Look at byte[1] (FC) in buffer to determine expected length:
   - FC 3 resp: [slave fc byte_count N data CRC_lo CRC_hi] = 3 + N + 2
   - FC 5/6 req/resp: 8 bytes total
   - FC 16 resp: 8 bytes
   - FC 0x19 heartbeat: 4 bytes (slave fc CRC_lo CRC_hi)
   - fc | 0x80 exception (known FCs only): 5 bytes (slave fc errcode CRC_lo CRC_hi)
3. Unknown FC / parse error -> increment resync counter, advance buffer 1 byte
4. idle_ms timeout with un-parsed buffer -> drop all + metric
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator

_ASCII_GARBAGE_RE = re.compile(rb"[\r\n][A-Z#@!][\w=:.,]*[\r\n]?")

# minimum bytes needed before we can dispatch on FC
_MIN_DISPATCH_LEN = 4
# FC 3 variable-length: need at least 3 bytes (slave + fc + byte_count)
_FC3_HEADER_LEN = 3
# FC 3 variable-length total overhead: slave(1) + fc(1) + byte_count(1) + CRC(2)
_FC3_OVERHEAD = 5
# exception response fixed length
_EXCEPTION_FRAME_LEN = 5
# function code for FC 3 (read holding registers)
_FC_READ_HOLDING = 0x03
# exception bit mask
_FC_EXCEPTION_BIT = 0x80

# expected frame length by FC (total bytes including slave + CRC)
_FIXED_LEN_BY_FC: dict[int, int] = {
    0x05: 8,  # write single coil req/resp
    0x06: 8,  # write single holding req/resp
    0x10: 8,  # write multiple holding response
    0x19: 4,  # heartbeat (slave fc CRC_lo CRC_hi)
}

# base FCs that may have Modbus exception responses (fc | 0x80).
# vendor-specific codes like 0x19 are excluded — they don't generate standard exceptions.
_KNOWN_BASE_FCS: frozenset[int] = frozenset({0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x0F, 0x10})


class Framer:
    def __init__(self, *, idle_ms: int = 300) -> None:
        self._buf = bytearray()
        self._ready: deque[bytes] = deque()
        self._last_ingest_ms: int = 0
        self._idle_ms = idle_ms
        self.stats: dict[str, int] = {"resync": 0, "heartbeat_stripped": 0}

    def feed(self, data: bytes, *, now_ms: int = 0) -> None:
        """Feed raw bytes into the framer. Strips ASCII garbage first."""
        stripped, n = _ASCII_GARBAGE_RE.subn(b"", data)
        if n > 0:
            self.stats["heartbeat_stripped"] += n
        self._buf.extend(stripped)
        self._last_ingest_ms = now_ms
        self._try_parse()

    def tick(self, now_ms: int) -> None:
        """Call periodically. Flushes buffer if idle_ms exceeded without a successful parse."""
        if self._buf and (now_ms - self._last_ingest_ms) > self._idle_ms:
            self._buf.clear()
            self.stats["resync"] += 1

    def pop_frames(self) -> Iterator[bytes]:
        """Yield all complete frames accumulated since last call."""
        while self._ready:
            yield self._ready.popleft()

    def buffer_len(self) -> int:
        """Return current buffer length (useful for tests and metrics)."""
        return len(self._buf)

    def _try_parse(self) -> None:
        while True:
            if len(self._buf) < _MIN_DISPATCH_LEN:
                return
            fc = self._buf[1]
            length = self._expected_length(fc)
            if length is None:
                # variable-length: FC 3 uses bytecount field at buf[2]
                if fc == _FC_READ_HOLDING:
                    if len(self._buf) < _FC3_HEADER_LEN:
                        return
                    byte_count = self._buf[2]
                    length = byte_count + _FC3_OVERHEAD
                elif (fc & _FC_EXCEPTION_BIT) and (fc & ~_FC_EXCEPTION_BIT) in _KNOWN_BASE_FCS:
                    length = _EXCEPTION_FRAME_LEN  # slave fc errcode CRC_lo CRC_hi
                else:
                    # unknown variable FC — advance 1 byte + resync
                    del self._buf[0]
                    self.stats["resync"] += 1
                    continue
            if len(self._buf) < length:
                return
            frame = bytes(self._buf[:length])
            del self._buf[:length]
            self._ready.append(frame)

    @staticmethod
    def _expected_length(fc: int) -> int | None:
        """Return fixed frame length for known FC, or None for variable-length."""
        return _FIXED_LEN_BY_FC.get(fc)
