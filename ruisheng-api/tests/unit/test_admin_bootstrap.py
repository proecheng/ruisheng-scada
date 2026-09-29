"""Input and disclosure boundary for the maintenance-only bootstrap CLI."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from ruisheng_api import admin_bootstrap as bootstrap

PASSWORD = "unique_test_only_" + "A" * 15


def payload(**changes: object) -> bytes:
    value = {
        "schema_version": 1,
        "action": "create",
        "operation_id": "bcfe4c79-71a2-458e-b90a-4ea49c9983ac",
        "site_id": "site-test",
        "user_name": "rs_admin",
        "password": PASSWORD,
        **changes,
    }
    return json.dumps(value).encode()


def test_request_is_bounded_and_password_is_not_in_repr() -> None:
    request = bootstrap.read_request(io.BytesIO(payload()))
    assert request.password == PASSWORD
    assert PASSWORD not in repr(request)
    assert set(request.receipt("created")) == {"operation_id", "site_id", "user_name", "status"}
    assert PASSWORD not in json.dumps(request.audit_context())
    padded = payload() + b" " * (4096 - len(payload()))
    assert bootstrap.read_request(io.BytesIO(padded)) == request
    with pytest.raises(bootstrap.BootstrapRejected):
        bootstrap.read_request(io.BytesIO(padded + b" "))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 1.0},
        {"schema_version": 2},
        {"extra": "forbidden"},
        {"action": "reset"},
        {"operation_id": "secret-not-a-uuid"},
        {"operation_id": "bcfe4c79-71a2-158e-b90a-4ea49c9983ac"},
        {"site_id": "other"},
        {"site_id": "site-test\n"},
        {"site_id": "site-" + "a" * 45},
        {"user_name": "admin@example.invalid"},
        {"user_name": "rs_admin\n"},
        {"password": "x" * 31},
        {"password": "x" * 33},
        {"password": "x" * 31 + " "},
        {"password": "x" * 31 + "\u00e9"},
        {"password": None},
    ],
)
def test_invalid_inputs_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(bootstrap.BootstrapRejected) as caught:
        bootstrap.read_request(io.BytesIO(payload(**changes)))
    assert str(caught.value) == ""


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"\xff",
        b"{" * 4000,
        payload()[:-1] + b',"password":"duplicate"}',
        payload().replace(b'"schema_version": 1', b'"schema_version": NaN'),
        payload().replace(b'"schema_version": 1', b'"schema_version": Infinity'),
    ],
)
def test_malformed_or_duplicate_json_rejected(raw: bytes) -> None:
    with pytest.raises(bootstrap.BootstrapRejected):
        bootstrap.read_request(io.BytesIO(raw))


@pytest.mark.parametrize(
    ("exception", "status", "exit_code"),
    [
        (RuntimeError(f"SQL parameters: {PASSWORD}"), "unknown", 3),
        (bootstrap.BootstrapRejected(), "rejected", 2),
    ],
)
def test_cli_never_renders_exception_parameters(
    monkeypatch, capsys, exception: Exception, status: str, exit_code: int
) -> None:
    monkeypatch.setattr(bootstrap.sys, "argv", ["admin_bootstrap"])
    monkeypatch.setattr(bootstrap.sys, "stdin", io.TextIOWrapper(io.BytesIO(payload())))
    monkeypatch.setattr(bootstrap, "run", AsyncMock(side_effect=exception))
    assert bootstrap.main() == exit_code
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
    assert not output.err
    assert json.loads(output.out)["status"] == status


def test_cli_rejects_arguments_without_echoing_them(monkeypatch, capsys) -> None:
    monkeypatch.setattr(bootstrap.sys, "argv", ["admin_bootstrap", PASSWORD])
    run = AsyncMock()
    monkeypatch.setattr(bootstrap, "run", run)
    assert bootstrap.main() == 2
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
    run.assert_not_called()


@pytest.mark.parametrize("status", ["created", "confirmed", "empty", "conflict"])
def test_cli_allowlisted_receipts(monkeypatch, capsys, status: str) -> None:
    request = bootstrap.read_request(io.BytesIO(payload()))
    monkeypatch.setattr(bootstrap.sys, "argv", ["admin_bootstrap"])
    monkeypatch.setattr(bootstrap.sys, "stdin", io.TextIOWrapper(io.BytesIO(payload())))
    monkeypatch.setattr(bootstrap, "run", AsyncMock(return_value=request.receipt(status)))
    assert bootstrap.main() == (2 if status == "conflict" else 0)
    assert json.loads(capsys.readouterr().out) == request.receipt(status)


def test_identity_and_operation_are_in_audit_but_password_is_not() -> None:
    request = bootstrap.read_request(io.BytesIO(payload()))
    assert replace(request, operation_id=str(uuid4())).audit_context() != request.audit_context()
    assert replace(request, password="b" * 32).audit_context() == request.audit_context()


def test_no_startup_or_route_import() -> None:
    from pathlib import Path

    package = Path(bootstrap.__file__).parent
    for path in [package / "__main__.py", package / "main.py", *package.glob("api/**/*.py")]:
        assert "admin_bootstrap" not in path.read_text(encoding="utf-8")
