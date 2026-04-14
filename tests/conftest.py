"""根 conftest：目前仅提供标识 Windows 的 fixture。
数据库/Redis fixtures 在 Stage E 添加。"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def is_windows() -> bool:
    return sys.platform == "win32"
