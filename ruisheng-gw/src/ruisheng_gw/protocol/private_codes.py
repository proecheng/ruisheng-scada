"""Vendor-specific private FunCode registry (Plan 1 placeholder).

Plan 1 intentionally ships NO vendor decoders for FC 13/26. Real vendor
layouts vary wildly (e.g., some use FC 13 = read with timestamp, adding
6-8 bytes before data). Naive alias to FC 3/6 would silently corrupt
data on CRC-coincidence cases. When Plan 1.5 encounters real vendors,
extend this registry with per-vendor decoders.
"""

from __future__ import annotations

from collections.abc import Callable

from ruisheng_gw.protocol.exceptions import PrivateCodeNotImplemented

VENDOR_ID_STANDARD = "standard"

_registry: dict[tuple[str, int], Callable[[bytes], object]] = {}


def register_vendor(vendor_id: str, *, fc: int, decoder: Callable[[bytes], object]) -> None:
    """Register a vendor-specific decoder for a private function code."""
    _registry[(vendor_id, fc)] = decoder


def resolve_vendor_decoder(vendor_id: str, *, fc: int) -> Callable[[bytes], object]:
    """Return decoder for (vendor_id, fc). If not registered, returns a BLOCKED callable."""
    d = _registry.get((vendor_id, fc))
    if d is not None:
        return d

    def _blocked(_: bytes) -> object:
        raise PrivateCodeNotImplemented(
            f"vendor={vendor_id!r} fc=0x{fc:02X} not implemented "
            f"(Plan 1 does not ship private code decoders; Plan 1.5 scope)"
        )

    return _blocked
