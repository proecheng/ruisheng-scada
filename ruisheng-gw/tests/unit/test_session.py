"""Session map: keyed by dev_number (globally unique per shared ORM UQ).

- bind(dev_number, writer, bus_id) — idempotent; overwrites writer + bumps generation
- get(dev_number) → (writer, generation_id, bus_id) or None
- remove(dev_number)
- Reconnect scenario: new writer arrives → swap + gen++, bus_id unchanged
"""

from __future__ import annotations

from ruisheng_gw.transport.session import SessionMap


def test_bind_and_get() -> None:
    sm = SessionMap()
    sm.bind(dev_number="DEV-001", writer="W1", bus_id="1.2.3.4")  # type: ignore[arg-type]
    entry = sm.get("DEV-001")
    assert entry is not None
    assert entry.writer == "W1"
    assert entry.generation == 1
    assert entry.bus_id == "1.2.3.4"


def test_rebind_swaps_writer_and_bumps_generation() -> None:
    sm = SessionMap()
    sm.bind(dev_number="DEV-001", writer="W1", bus_id="1.2.3.4")  # type: ignore[arg-type]
    sm.bind(dev_number="DEV-001", writer="W2", bus_id="1.2.3.4")  # type: ignore[arg-type]
    entry = sm.get("DEV-001")
    assert entry.writer == "W2"
    assert entry.generation == 2  # noqa: PLR2004  # second bind → generation 2


def test_rebind_preserves_bus_id_even_if_new_specified() -> None:
    """v2 B1: reconnect from new ephemeral port must NOT change bus_id."""
    sm = SessionMap()
    sm.bind(dev_number="DEV-001", writer="W1", bus_id="1.2.3.4:6001")  # type: ignore[arg-type]
    sm.bind(dev_number="DEV-001", writer="W2", bus_id="1.2.3.4:6002")  # type: ignore[arg-type]
    entry = sm.get("DEV-001")
    # bus_id is keyed by dtu_ip only per v2 B1 design; port change ignored
    assert entry.bus_id == "1.2.3.4:6001"


def test_remove() -> None:
    sm = SessionMap()
    sm.bind(dev_number="DEV-001", writer="W1", bus_id="1.2.3.4")  # type: ignore[arg-type]
    sm.remove("DEV-001")
    assert sm.get("DEV-001") is None
