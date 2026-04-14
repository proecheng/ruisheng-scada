"""润盛 IoT 共享库 — 模型、枚举、错误码、常量。

所有业务服务（ruisheng-api / ruisheng-gw）启动时必须检查
SHARED_SCHEMA_VERSION == REQUIRED，不匹配则拒绝启动。
"""

# 格式：YYYYMMDD + 当天递增 2 位 → 20260413
# 规则：任何 breaking change（字段删/改类型/必填新增）必须 +1 并更新 CHANGELOG
SHARED_SCHEMA_VERSION: int = 20260414

__version__ = "0.1.0"
__all__ = ["SHARED_SCHEMA_VERSION"]
