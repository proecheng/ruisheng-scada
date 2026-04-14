"""枚举集合。任何新增 enum 都在此重导出以便 `from ruisheng_shared.enums import X`。

B2 onwards will append imports here incrementally as each enum file is added.
"""

from .alarm_type import AlarmType
from .fun_code import FunCode

__all__: list[str] = ["AlarmType", "FunCode"]
