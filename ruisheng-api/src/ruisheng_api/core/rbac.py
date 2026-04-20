"""4 级 RBAC + control_authority 位检查。对应 spec §3.6。"""

from __future__ import annotations

from dataclasses import dataclass

from ruisheng_shared.errors.codes import BizError, ErrCode


@dataclass(frozen=True)
class CurrentUser:
    user_name: str
    usr_group: str
    role: str
    control_authority: int
    jti: str
    fp: str


def check_role(user: CurrentUser, *, allowed: tuple[str, ...]) -> None:
    if user.role not in allowed:
        raise BizError(ErrCode.FORBIDDEN, f"role {user.role!r} not in {allowed}")


def check_ca(user: CurrentUser, *, bit: int) -> None:
    if not (user.control_authority & bit):
        raise BizError(ErrCode.FORBIDDEN, f"missing control_authority bit 0x{bit:x}")
