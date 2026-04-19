"""REST 路由。mount_routers() 聚合挂载。"""

from fastapi import FastAPI

from . import health


def mount_routers(app: FastAPI) -> None:
    app.include_router(health.router)
