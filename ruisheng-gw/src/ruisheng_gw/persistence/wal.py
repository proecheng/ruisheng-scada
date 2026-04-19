"""WAL file fallback for batch_writer.

Format: `{wal_dir}/YYYYMMDD-HHMM.ndjson` — newline-delimited JSON.
- rotate when single file > single_file_mb
- drop oldest files when total size > total_gb
- startup replay: read all, feed sink, delete after success
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from ruisheng_gw.persistence.batch_writer import BatchRow


def _default_wal_dir() -> str:
    if sys.platform == "win32":
        return r"D:\ruisheng\gw\wal"
    return "/var/log/ruisheng/gw/wal"


class Wal:
    def __init__(
        self,
        *,
        wal_dir: str | None = None,
        single_file_mb: float = 1024.0,
        total_gb: float = 10.0,
    ) -> None:
        self._dir = Path(wal_dir if wal_dir is not None else _default_wal_dir())
        self._dir.mkdir(parents=True, exist_ok=True)
        self._single_file_bytes = int(single_file_mb * 1024 * 1024)
        self._total_bytes = int(total_gb * 1024 * 1024 * 1024)

    def _current_file(self) -> Path:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M")
        return self._dir / f"{ts}.ndjson"

    def _all_files_sorted(self) -> list[Path]:
        paths = list(self._dir.glob("*.ndjson"))

        def _sort_key(p: Path) -> tuple[str, int]:
            stem = p.stem  # e.g., "20260419-1000" or "20260419-1000-1"
            # match base timestamp and optional numeric suffix
            m = re.match(r"^(\d{8}-\d{4})(?:-(\d+))?$", stem)
            if m:
                return (m.group(1), int(m.group(2) or 0))
            return (stem, 0)

        return sorted(paths, key=_sort_key)

    async def append(self, rows: list[BatchRow]) -> None:
        if not rows:
            return
        current = self._current_file()
        if current.exists() and current.stat().st_size > self._single_file_bytes:
            current = self._rotate(current)
        for r in rows:
            line = (
                json.dumps(
                    {
                        "dev_number": r.dev_number,
                        "point_id": r.point_id,
                        "rt_value": r.rt_value,
                        "org_value": r.org_value,
                        "recorded_at": r.recorded_at,
                    }
                )
                + "\n"
            )
            with current.open("a", encoding="utf-8") as f:
                f.write(line)
            if current.stat().st_size > self._single_file_bytes:
                current = self._rotate(current)
        self._enforce_total_limit()

    def _rotate(self, current: Path) -> Path:
        suffix = 1
        while True:
            alt = current.with_name(f"{current.stem}-{suffix}.ndjson")
            if not alt.exists():
                return alt
            suffix += 1

    def _enforce_total_limit(self) -> None:
        files = self._all_files_sorted()
        total = sum(f.stat().st_size for f in files)
        while total > self._total_bytes and files:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)

    async def replay_and_cleanup(
        self,
        *,
        sink: Callable[[list[BatchRow]], Awaitable[None]],
    ) -> None:
        for f in self._all_files_sorted():
            rows: list[BatchRow] = []
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    rows.append(
                        BatchRow(
                            dev_number=d["dev_number"],
                            point_id=d["point_id"],
                            rt_value=d["rt_value"],
                            org_value=d["org_value"],
                            recorded_at=d["recorded_at"],
                        )
                    )
                except Exception:
                    pass  # skip malformed lines
            if rows:
                await sink(rows)
            f.unlink(missing_ok=True)
