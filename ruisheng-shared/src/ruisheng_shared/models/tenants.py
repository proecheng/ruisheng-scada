"""租户表（微信公众号级）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class WxGroup(Base, TimestampMixin):
    __tablename__ = "wx_groups"

    usr_group: Mapped[str] = mapped_column(String(50), primary_key=True)
    appid: Mapped[str | None] = mapped_column(String(50))
    appsecret: Mapped[str | None] = mapped_column(String(100))
    token: Mapped[str | None] = mapped_column(String(200))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    template_id: Mapped[str | None] = mapped_column(String(50))
    company_name: Mapped[str | None] = mapped_column(String(100))
    sys_title: Mapped[str | None] = mapped_column(String(100))
    remark: Mapped[str | None] = mapped_column(String(255))
