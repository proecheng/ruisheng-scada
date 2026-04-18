"""ruisheng-gw 启动入口。

- schema mismatch → exit 1
- alembic mismatch → exit 2
- config invalid → exit 3
- graceful shutdown → exit 0
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


# hardcoded literal — 与 G7 #28-B 两版本字段分离一致
# 升 shared 时 gw PR 必须同步改此常量
REQUIRED_SHARED_SCHEMA_VERSION: int = 20260415
EXPECTED_ALEMBIC_HEAD: str = "959079e6cae9"


def check_shared_schema_version(required: int = REQUIRED_SHARED_SCHEMA_VERSION) -> None:
    from ruisheng_shared import SHARED_SCHEMA_VERSION  # noqa: PLC0415

    if required != SHARED_SCHEMA_VERSION:
        raise RuntimeError(f"shared mismatch: expect {required}, got {SHARED_SCHEMA_VERSION}")


async def check_alembic_head(
    engine: AsyncEngine,
    expected: str = EXPECTED_ALEMBIC_HEAD,
) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    async with engine.begin() as conn:
        row = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current = row.scalar_one_or_none()
    if current != expected:
        raise RuntimeError(f"alembic mismatch: expect {expected}, got {current}")


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="ruisheng-gw")
    parser.add_argument(
        "--check-only", action="store_true", help="startup checks only, then exit 0"
    )
    parser.add_argument(
        "--print-config", action="store_true", help="print resolved config and exit"
    )
    args = parser.parse_args()

    try:
        check_shared_schema_version()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.check_only:
        print("ok: shared schema version matches")
        return 0

    if args.print_config:
        from pydantic import ValidationError  # noqa: PLC0415

        from ruisheng_gw.config import Config  # noqa: PLC0415

        try:
            cfg = Config()
        except ValidationError as e:
            print(f"ERROR: config invalid: {e}", file=sys.stderr)
            return 3
        print(cfg.model_dump_json(indent=2))
        return 0

    # TODO A4+ — structlog + health + run_server (后续 task)
    print("ruisheng-gw main not yet fully implemented (Stage A4+)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
