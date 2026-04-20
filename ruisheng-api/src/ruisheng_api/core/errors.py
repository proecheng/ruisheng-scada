"""统一异常注册器。将 BizError / ValidationError / 未捕获异常转 ApiResponse。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from ruisheng_shared.errors.codes import BizError, ErrCode

from .response import fail


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz(_: Request, exc: BizError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=fail(exc.code, exc.msg).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=fail(ErrCode.BAD_PARAM, str(exc.errors()[:3])).model_dump(),
        )

    @app.exception_handler(Exception)
    async def _generic(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception: {!r}", exc)
        return JSONResponse(
            status_code=500,
            content=fail(ErrCode.INTERNAL, "internal server error").model_dump(),
        )
