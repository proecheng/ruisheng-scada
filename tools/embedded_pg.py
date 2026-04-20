"""Windows 嵌入式 PostgreSQL 启动器（无 Docker Desktop 场景）。

使用 postgresql-binaries 包（pypi: postgresql-15.x-win）或手工下载 portable。
这里先用 pg_tmp 风格最简实现：用 pip 包 pg_embed 或降级到 pytest-postgresql。
当前为 stub；E1 `postgres_url` fixture 同步模式需 `start_sync()` / `stop_sync()` 入口。
"""

from __future__ import annotations

import asyncio
import random
import tempfile
from pathlib import Path

_NOT_IMPLEMENTED_MSG = (
    "EmbeddedPostgres 目前为 stub。"
    "真正实现在 Plan 0 后续迭代（pg_tmp / pg_embed / portable binaries）。"
    "当前 Windows 用户请启用 Docker Desktop 或清除 USE_EMBEDDED_PG（default=container 模式）。"
)


class EmbeddedPostgres:
    def __init__(self, version: str = "15") -> None:
        self.version = version
        self.port = random.randint(15000, 30000)
        self.data_dir = Path(tempfile.mkdtemp(prefix="ruisheng-pg-"))
        self.url = f"postgresql+asyncpg://postgres:postgres@127.0.0.1:{self.port}/ruisheng"
        self._proc: asyncio.subprocess.Process | None = None

    # Sync API（E1 postgres_url fixture 用）
    def start_sync(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def stop_sync(self) -> None:
        if self._proc:
            self._proc.terminate()

    # Async API（未来 async 场景）
    async def start(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            await self._proc.wait()
