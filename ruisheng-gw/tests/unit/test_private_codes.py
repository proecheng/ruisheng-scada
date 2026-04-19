"""FC 13 (0x0D) / 26 (0x1A) private codes: BLOCKED placeholder.

Plan 1 does NOT implement vendor-specific FC 13/26 layouts (see spec v2 A5).
Vendors differ (e.g., some use FC 13 for 'read with timestamp' which adds
6-8 extra bytes — naive alias to FC 3 would mis-parse and store garbage).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
import ruisheng_gw.protocol.private_codes as _pcc_mod
from ruisheng_gw.protocol.exceptions import PrivateCodeNotImplemented
from ruisheng_gw.protocol.private_codes import (
    VENDOR_ID_STANDARD,
    register_vendor,
    resolve_vendor_decoder,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    """Save and restore module-level _registry to prevent test pollution."""
    original = dict(_pcc_mod._registry)
    yield
    _pcc_mod._registry.clear()
    _pcc_mod._registry.update(original)


@pytest.mark.parametrize("fc", [0x0D, 0x1A])
def test_standard_vendor_raises_not_implemented(fc: int) -> None:
    # Plan 1: no standard vendor decoder exists
    decoder = resolve_vendor_decoder(VENDOR_ID_STANDARD, fc=fc)
    with pytest.raises(PrivateCodeNotImplemented):
        decoder(b"\x01\x0d\x00\x00")


def test_register_vendor_and_resolve() -> None:
    # Future vendor plugin pattern
    def _fake_decoder(body: bytes) -> object:
        return ("fake", body)

    register_vendor("acme", fc=0x0D, decoder=_fake_decoder)
    decoder = resolve_vendor_decoder("acme", fc=0x0D)
    assert decoder(b"\x01\x0d\xaa\xbb") == ("fake", b"\x01\x0d\xaa\xbb")
