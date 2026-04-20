"""按字典序跑 seeds/ 下所有 .sql 文件。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]  # asyncpg 无 py.typed marker

SEEDS_DIR = Path(__file__).parent.parent / "seeds"


async def main() -> None:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng",
    ).replace("+asyncpg", "")
    conn = await asyncpg.connect(url)
    try:
        for sql_file in sorted(SEEDS_DIR.glob("*.sql")):
            print(f"[seed] {sql_file.name}")
            await conn.execute(sql_file.read_text(encoding="utf-8"))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
