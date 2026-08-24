"""Management Bearer token parsing and digest comparison."""

from __future__ import annotations

import hashlib

import pytest
from ruisheng_api.core import management_auth

TOKEN = "a" * 43
DIGEST = hashlib.sha256(TOKEN.encode("ascii")).hexdigest()


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Basic " + TOKEN,
        "Bearer " + "a" * 42,
        "Bearer " + "a" * 257,
        "Bearer " + "a" * 21 + " " + "a" * 22,
        "Bearer " + "a" * 42 + "é",
        "Bearer " + "a" * 42 + "!",
    ],
)
def test_rejects_missing_malformed_or_out_of_range_tokens(authorization: str | None) -> None:
    assert not management_auth.management_bearer_matches(authorization, DIGEST)


def test_uses_constant_time_digest_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(management_auth.secrets, "compare_digest", _compare)
    assert management_auth.management_bearer_matches(f"Bearer {TOKEN}", DIGEST)
    assert calls == [(DIGEST, DIGEST)]


def test_missing_digest_fails_closed() -> None:
    assert not management_auth.management_bearer_matches(f"Bearer {TOKEN}", None)
