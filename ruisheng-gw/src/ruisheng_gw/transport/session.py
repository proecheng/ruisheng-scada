"""Per-gw-process session map: dev_number → (writer, generation_id, bus_id).

v2 B1: session key is `dev_number` (globally unique per shared ORM UQ).
v2 B7: writer has a generation_id; poller must re-lookup per poll to
detect staleness after DTU reconnect.

bus_id stability: set once on first bind(), preserved on rebind (TCP
reconnect from new ephemeral port does NOT change bus_id — see v2 B1
discussion). 1 DTU = 1 RS485 bus assumption; Plan 1.5 may revisit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionEntry:
    writer: asyncio.StreamWriter
    generation: int
    bus_id: str


class SessionMap:
    def __init__(self) -> None:
        self._map: dict[str, SessionEntry] = {}

    def bind(
        self,
        *,
        dev_number: str,
        writer: asyncio.StreamWriter,
        bus_id: str,
    ) -> SessionEntry:
        existing = self._map.get(dev_number)
        if existing is None:
            entry = SessionEntry(writer=writer, generation=1, bus_id=bus_id)
        else:
            # v2 B1 — preserve bus_id even if caller passes new value
            entry = SessionEntry(
                writer=writer,
                generation=existing.generation + 1,
                bus_id=existing.bus_id,
            )
        self._map[dev_number] = entry
        return entry

    def get(self, dev_number: str) -> SessionEntry | None:
        return self._map.get(dev_number)

    def remove(self, dev_number: str) -> None:
        self._map.pop(dev_number, None)

    def __len__(self) -> int:
        return len(self._map)
