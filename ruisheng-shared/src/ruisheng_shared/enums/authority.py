"""4 级 RBAC 权限。对应 spec §3.6。"""

from __future__ import annotations

from enum import Enum


class Authority(str, Enum):
    USER = "User"
    COMPANY = "Company"
    GROUP_COMPANY = "GroupCompany"
    ADMIN = "Administrators"

    @property
    def level(self) -> int:
        return {"User": 1, "Company": 2, "GroupCompany": 3, "Administrators": 4}[self.value]

    def is_below(self, other: Authority) -> bool:
        """严格小于（不含等于）。"""
        return self.level < other.level
