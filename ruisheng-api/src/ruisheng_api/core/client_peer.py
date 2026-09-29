"""Identify the immediate peer without trusting client-written forwarding headers."""

from __future__ import annotations

import ipaddress


def is_shared_ingress(peer: str, trusted_proxy_cidrs: str) -> bool:
    """A peer inside the trusted proxy network stands for every browser behind it."""
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for item in trusted_proxy_cidrs.split(","):
        network = item.strip()
        if network and address in ipaddress.ip_network(network, strict=False):
            return True
    return False
