"""api 服务配置。API_ 前缀环境变量；启动时严格校验。"""

from __future__ import annotations

import os
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="API_",
        extra="forbid",
        case_sensitive=False,
    )

    listen_host: str = Field(default="127.0.0.1")
    listen_port: int = Field(default=8000, ge=1, le=65535)

    db_url: str = Field(..., description="ruisheng_api role async URL")
    gw_db_url: str = Field(..., description="ruisheng_gw BYPASSRLS role for wxpay callback")
    redis_url: str = Field(..., description="redis-py URL")

    jwt_secret: str = Field(..., min_length=32)
    jwt_access_ttl_sec: int = Field(default=900, ge=60)
    jwt_refresh_ttl_sec: int = Field(default=7 * 24 * 3600, ge=3600)
    otp_ttl_sec: int = Field(default=300, ge=60)

    db_pool_size: int = Field(default=20, ge=1)
    db_pool_overflow: int = Field(default=10, ge=0)

    login_fail_user_max: int = Field(default=5, ge=1)
    login_fail_user_window_sec: int = Field(default=300, ge=30)
    login_lock_ttl_sec: int = Field(default=1800, ge=60)
    login_fail_ip_max: int = Field(default=20, ge=1)
    ip_block_ttl_sec: int = Field(default=3600, ge=60)

    slowapi_rate_default: str = Field(default="100/minute")
    slowapi_rate_login: str = Field(default="5/minute")

    env: str = Field(default="dev")  # dev / test / prod

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_api_env(cls, values: Any) -> Any:
        known = {name.upper() for name in cls.model_fields}
        unknown = sorted(
            k for k in os.environ if k.startswith("API_") and k[4:].upper() not in known
        )
        if unknown:
            raise ValueError(f"extra unknown API_ env vars: {unknown}")
        return values
