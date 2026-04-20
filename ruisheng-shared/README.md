# ruisheng-shared

润盛 IoT 平台的共享 Python 包：
- SQLAlchemy 2.0 ORM 模型（23 张表）
- Pydantic schemas（API 请求/响应 + WS 信封）
- enums（FunCode / AlarmType / AlarmAction / ControlStatus / Authority）
- errors（ErrCode + BizError）
- constants（CRC 多项式、端口、TTL）
- validators（RS485 波特率约束表）

## 启动检查

```python
from ruisheng_shared import SHARED_SCHEMA_VERSION
REQUIRED = 20260413
if SHARED_SCHEMA_VERSION != REQUIRED:
    raise RuntimeError(f"shared version mismatch: {SHARED_SCHEMA_VERSION} != {REQUIRED}")
```
