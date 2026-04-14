"""ORM 模型集合。
每次新增或修改模型必须在 CHANGELOG.md 登记；如为 breaking 需升级 SHARED_SCHEMA_VERSION。
"""

from .base import Base, SoftDeleteMixin, TimestampMixin
from .tenants import WxGroup
from .users import User, UserEmail, UserPhoneNumber, UserWxBinding

__all__ = [
    "Base",
    "SoftDeleteMixin",
    "TimestampMixin",
    "User",
    "UserEmail",
    "UserPhoneNumber",
    "UserWxBinding",
    "WxGroup",
]
