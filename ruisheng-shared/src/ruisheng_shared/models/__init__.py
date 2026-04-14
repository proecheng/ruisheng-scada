"""ORM 模型集合。
每次新增或修改模型必须在 CHANGELOG.md 登记；如为 breaking 需升级 SHARED_SCHEMA_VERSION。
"""

from .alarms import AlarmOutbox, AlarmRecord, DeviceWaringCfg
from .base import Base, SoftDeleteMixin, TimestampMixin
from .control import UserControlAction
from .devices import Device, DevicePoint, DeviceStaticData, DeviceTemplate, SimCard
from .plans import MaintainAction, MaintainPlan, TimingPlan
from .tenants import WxGroup
from .users import User, UserEmail, UserPhoneNumber, UserWxBinding

__all__ = [
    "AlarmOutbox",
    "AlarmRecord",
    "Base",
    "Device",
    "DevicePoint",
    "DeviceStaticData",
    "DeviceTemplate",
    "DeviceWaringCfg",
    "MaintainAction",
    "MaintainPlan",
    "SimCard",
    "SoftDeleteMixin",
    "TimestampMixin",
    "TimingPlan",
    "User",
    "UserControlAction",
    "UserEmail",
    "UserPhoneNumber",
    "UserWxBinding",
    "WxGroup",
]
