"""Protocol exceptions (raised by codec + framer)."""

from __future__ import annotations


class ProtocolError(Exception):
    """Base for all protocol errors."""


class CRCMismatchError(ProtocolError):
    """CRC16 verification failed."""


class FramingError(ProtocolError):
    """Framing buffer overflow or malformed length."""


class PrivateCodeNotImplementedError(ProtocolError):
    """Private FunCode encountered; vendor decode not yet implemented."""
