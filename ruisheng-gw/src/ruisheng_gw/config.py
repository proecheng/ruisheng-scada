"""gw 配置：pydantic-settings + extra=forbid + 启动时严格校验。"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SerialPortConfig(BaseModel):
    """Single serial port bus configuration."""

    port: str
    baud_rate: int = 9600


class Config(BaseSettings):
    """gw 运行时配置。所有字段 `GW_` 前缀从环境读。"""

    model_config = SettingsConfigDict(
        env_prefix="GW_",
        extra="forbid",  # 对 .env / 直接 kwargs 生效
        case_sensitive=False,
    )

    listen_host: str = Field(..., description="TCP server bind host")
    listen_port: int = Field(..., ge=1, le=65535)
    database_url: str = Field(..., description="asyncpg URL")
    redis_url: str = Field(..., description="redis URL")

    health_port: int = Field(default=9090, ge=1, le=65535)

    poll_concurrency_per_bus: int = Field(default=1, ge=1, le=1)  # RS485 物理约束：1

    batch_queue_maxsize: int = Field(default=10000, ge=100)
    batch_flush_ms: int = Field(default=100, ge=10)
    batch_flush_rows: int = Field(default=500, ge=1)

    heartbeat_timeout_sec: int = Field(default=90, ge=1)  # 3× 30s 默认
    bus_lock_timeout_sec: int = Field(default=15, ge=1)

    serial_ports: list[SerialPortConfig] = Field(
        default_factory=list,
        description='JSON list, e.g. [{"port":"COM3","baud_rate":9600}]',
    )

    @model_validator(mode="after")
    def _reject_duplicate_serial_ports(self) -> Config:
        seen: set[str] = set()
        for sp in self.serial_ports:
            if sp.port in seen:
                raise ValueError(f"duplicate serial port in config: {sp.port}")
            seen.add(sp.port)
        return self

    wal_dir: str = Field(default="/var/log/ruisheng/gw/wal")  # Windows 由 wal.py 改写
    wal_single_file_mb: int = Field(default=1024, ge=10)
    wal_total_gb: int = Field(default=10, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_gw_env(cls, values: Any) -> Any:
        """v2 D7：扫 os.environ 所有 GW_* 键，与声明字段比对；未知键 raise。

        pydantic-settings `extra="forbid"` 不扫 os.environ（上游 intended），
        须手工补 D7 防御深度。"""
        known_upper = {name.upper() for name in cls.model_fields}
        unknown = sorted(
            key
            for key in os.environ
            if key.startswith("GW_") and key[3:].upper() not in known_upper
        )
        if unknown:
            raise ValueError(f"extra unknown GW_ env vars (v2 D7): {unknown}")
        return values
