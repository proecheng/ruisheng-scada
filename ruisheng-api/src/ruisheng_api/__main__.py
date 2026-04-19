"""`python -m ruisheng_api` 启 uvicorn。"""

from __future__ import annotations

import uvicorn  # type: ignore[import-not-found]

from .config import Config


def main() -> None:
    cfg = Config()
    uvicorn.run(
        "ruisheng_api.main:create_app",
        factory=True,
        host=cfg.listen_host,
        port=cfg.listen_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
