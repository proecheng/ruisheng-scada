"""REST 路由。mount_routers() 聚合挂载。"""

from fastapi import FastAPI

from . import (
    alarms,
    auth,
    control,
    devices,
    health,
    orgs,
    pay,
    plans,
    points,
    reports,
    scenes,
    waveforms,
    ws,
)


def mount_routers(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(devices.router)
    app.include_router(control.router)
    app.include_router(control.query_router)
    app.include_router(points.router)
    app.include_router(alarms.cfg_router)
    app.include_router(alarms.record_router)
    app.include_router(ws.router)
    app.include_router(orgs.router)
    app.include_router(reports.router)
    app.include_router(waveforms.router)
    app.include_router(plans.timing_router)
    app.include_router(plans.maintenance_router)
    app.include_router(scenes.pages_router)
    app.include_router(scenes.views_router)
    app.include_router(pay.pay_router)
    app.include_router(pay.notify_router)
