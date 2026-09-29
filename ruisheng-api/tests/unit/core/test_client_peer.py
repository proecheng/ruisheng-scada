from ruisheng_api.core.client_peer import is_shared_ingress


def test_only_configured_proxy_network_is_shared_ingress() -> None:
    cidrs = "10.254.250.0/24, fd00:250::/64"
    assert is_shared_ingress("10.254.250.8", cidrs) is True
    assert is_shared_ingress("fd00:250::20", cidrs) is True
    assert is_shared_ingress("203.0.113.10", cidrs) is False
    assert is_shared_ingress("testclient", cidrs) is False
    assert is_shared_ingress("10.254.250.8", "") is False


def test_malformed_peer_is_not_treated_as_shared() -> None:
    assert is_shared_ingress("not-an-ip", "0.0.0.0/0") is False
