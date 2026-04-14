"""ORM 模型集合。
每次新增或修改模型必须在 CHANGELOG.md 登记；如为 breaking 需升级 SHARED_SCHEMA_VERSION。
"""

from .alarms import AlarmOutbox, AlarmRecord, DeviceWaringCfg
from .base import Base, SoftDeleteMixin, TimestampMixin
from .control import UserControlAction
from .devices import Device, DevicePoint, DeviceStaticData, DeviceTemplate, SimCard
from .pay import PayOrder, PayOrderSeen
from .plans import MaintainAction, MaintainPlan, TimingPlan

# NOTE: ScenePage / SceneView 仅 api 服务使用；gw（后台采集/控制）不得 import（spec §3.7）
from .scenes import ScenePage, SceneView
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
    "PayOrder",
    "PayOrderSeen",
    "ScenePage",
    "SceneView",
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
