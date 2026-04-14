"""Pydantic schemas（API 请求/响应 + WS 信封）。
详细业务 schemas 在 Plan 2 填充；本 Plan 0 只提供通用壳。
"""
from .common import ApiResponse

__all__ = ["ApiResponse"]
