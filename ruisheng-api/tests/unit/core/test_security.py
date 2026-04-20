import pytest
from ruisheng_api.core.security import (
    client_fingerprint,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    verify_password,
    verify_token,
)
from ruisheng_shared.errors.codes import BizError


def test_hash_and_verify():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_issue_and_verify_access():
    fp = client_fingerprint("1.1.1.1", "ua-1")
    t = issue_access_token("alice", "grp1", "User", 1, fp, secret="s" * 64, ttl_sec=900)
    payload = verify_token(t, secret="s" * 64, expected_fp=fp)
    assert payload["sub"] == "alice"
    assert payload["usr_group"] == "grp1"
    assert payload["role"] == "User"
    assert payload["ca"] == 1
    assert payload["fp"] == fp
    assert payload["typ"] == "access"


def test_verify_rejects_wrong_fingerprint():
    fp = client_fingerprint("1.1.1.1", "ua-1")
    t = issue_access_token("a", "g", "User", 0, fp, secret="s" * 64, ttl_sec=900)
    with pytest.raises(BizError):
        verify_token(t, secret="s" * 64, expected_fp="different")


def test_verify_rejects_expired():
    fp = client_fingerprint("x", "y")
    t = issue_access_token("a", "g", "User", 0, fp, secret="s" * 64, ttl_sec=-1)
    with pytest.raises(BizError):
        verify_token(t, secret="s" * 64, expected_fp=fp)


def test_refresh_token_has_typ_refresh():
    fp = client_fingerprint("x", "y")
    t = issue_refresh_token("a", "g", "User", 0, fp, secret="s" * 64, ttl_sec=3600)
    payload = verify_token(t, secret="s" * 64, expected_fp=fp, expected_typ="refresh")
    assert payload["typ"] == "refresh"


def test_fingerprint_stable():
    assert client_fingerprint("1.1.1.1", "ua") == client_fingerprint("1.1.1.1", "ua")
    assert client_fingerprint("1.1.1.1", "ua") != client_fingerprint("1.1.1.2", "ua")
