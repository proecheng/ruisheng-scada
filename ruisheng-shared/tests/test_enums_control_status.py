"""Spec §3.2 + §3.4.1 — 控制命令生命周期状态。"""

from __future__ import annotations

from ruisheng_shared.enums import ControlStatus


def test_all_values() -> None:
    assert {s.value for s in ControlStatus} == {
        "pending",
        "success",
        "failed",
        "timeout",
        "cancelled",
    }


def test_from_db_string() -> None:
    assert ControlStatus("pending") is ControlStatus.PENDING


def test_is_terminal() -> None:
    assert not ControlStatus.PENDING.is_terminal
    assert ControlStatus.SUCCESS.is_terminal
    assert ControlStatus.FAILED.is_terminal
    assert ControlStatus.TIMEOUT.is_terminal
    assert ControlStatus.CANCELLED.is_terminal
