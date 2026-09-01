from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

BASELINE_ARG_COUNT = 2
QUERY_ARG_COUNT = 4


def main() -> None:
    if len(sys.argv) not in (BASELINE_ARG_COUNT, QUERY_ARG_COUNT):
        raise RuntimeError(
            "expected database path, optionally followed by timestamp and baseline id"
        )
    audit_path = Path(sys.argv[1]).resolve(strict=True)
    if len(sys.argv) == BASELINE_ARG_COUNT:
        connection = sqlite3.connect(f"{audit_path.as_uri()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT coalesce(max(id),0) FROM requests").fetchone()
        finally:
            connection.close()
        print(
            json.dumps(
                {
                    "baseline_id": int(row[0]),
                    "captured_at": datetime.now(UTC).isoformat(timespec="microseconds"),
                },
                separators=(",", ":"),
            )
        )
        return
    try:
        baseline_id = int(sys.argv[3])
    except ValueError as error:
        raise RuntimeError("audit baseline id is invalid") from error
    if baseline_id < 0 or str(baseline_id) != sys.argv[3]:
        raise RuntimeError("audit baseline id is invalid")
    connection = sqlite3.connect(f"{audit_path.as_uri()}?mode=ro", uri=True)
    try:
        success_count = connection.execute(
            "SELECT count(*) FROM requests "
            "WHERE id>? AND at>=? AND path='/v1/attest' AND status=200",
            (baseline_id, sys.argv[2]),
        ).fetchone()[0]
        row = connection.execute(
            "SELECT id,at,path,status,decision,reason_code "
            "FROM requests WHERE id>? AND at>=? AND path='/v1/attest' AND status=200 "
            "ORDER BY id DESC LIMIT 1",
            (baseline_id, sys.argv[2]),
        ).fetchone()
    finally:
        connection.close()
    names = ("id", "at", "path", "status", "decision", "reason_code")
    print(
        json.dumps(
            {
                "success_count": success_count,
                "latest": dict(zip(names, row, strict=True)) if row else None,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
