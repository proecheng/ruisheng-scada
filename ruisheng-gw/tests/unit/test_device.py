"""Device state machine: UNREGISTERED → ONLINE → OFFLINE."""

from __future__ import annotations

import pytest
from ruisheng_gw.domain.device import Device, DeviceState, InvalidTransition


def test_initial_state_is_unregistered() -> None:
    d = Device(dev_number="DEV-001", usr_group="ug_A")
    assert d.state is DeviceState.UNREGISTERED


def test_register_transitions_to_online() -> None:
    d = Device(dev_number="DEV-001", usr_group="ug_A")
    d.register(now=100.0)
    assert d.state is DeviceState.ONLINE
    assert d.last_seen == 100.0  # noqa: PLR2004


def test_heartbeat_updates_last_seen_but_not_state() -> None:
    d = Device(dev_number="DEV-001", usr_group="ug_A")
    d.register(now=100.0)
    d.heartbeat(now=150.0)
    assert d.state is DeviceState.ONLINE
    assert d.last_seen == 150.0  # noqa: PLR2004


def test_timeout_transitions_online_to_offline() -> None:
    d = Device(dev_number="DEV-001", usr_group="ug_A")
    d.register(now=100.0)
    d.mark_offline(reason="heartbeat_timeout")
    assert d.state is DeviceState.OFFLINE


def test_illegal_transition_raises() -> None:
    d = Device(dev_number="DEV-001", usr_group="ug_A")
    with pytest.raises(InvalidTransition, match=r"cannot heartbeat"):
        d.heartbeat(now=100.0)  # cannot heartbeat before register


def test_re_register_from_offline_ok() -> None:
    d = Device(dev_number="DEV-001", usr_group="ug_A")
    d.register(now=100.0)
    d.mark_offline()
    d.register(now=200.0)
    assert d.state is DeviceState.ONLINE
    assert d.last_seen == 200.0  # noqa: PLR2004
