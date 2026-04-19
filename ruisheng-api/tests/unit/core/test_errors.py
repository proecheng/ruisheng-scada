from fastapi import FastAPI
from fastapi.testclient import TestClient
from ruisheng_api.core.errors import register_exception_handlers
from ruisheng_api.core.response import fail, ok
from ruisheng_shared.errors.codes import BizError, ErrCode


def test_ok_shape():
    r = ok(data={"x": 1})
    assert r.code == 0
    assert r.msg == "ok"
    assert r.data == {"x": 1}


def test_fail_shape():
    r = fail(ErrCode.BAD_PARAM, "bad")
    assert r.code == -100
    assert r.msg == "bad"
    assert r.data is None


def test_biz_error_handler_maps_http():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise BizError(ErrCode.DEV_OFFLINE, "offline")

    r = TestClient(app).get("/boom")
    assert r.status_code == 200  # ErrCode maps to HTTP 200 (biz error in body)
    body = r.json()
    assert body["code"] == -200
    assert body["msg"] == "offline"


def test_generic_500_returns_api_response():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("oops")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.json()["code"] == -300


def test_validation_error_maps_to_bad_param():
    app = FastAPI()
    register_exception_handlers(app)
    from pydantic import BaseModel

    class Body(BaseModel):
        x: int

    @app.post("/v")
    async def v(body: Body):
        return ok()

    r = TestClient(app).post("/v", json={"x": "nope"})
    assert r.status_code == 400
    assert r.json()["code"] == -100
