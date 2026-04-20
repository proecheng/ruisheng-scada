"""用户及关联表（4 张）。对应 spec §4.2 用户与权限。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            r"user_name ~ '^1[3-9][0-9]{9}$' OR user_name ~ '^[a-zA-Z][a-zA-Z0-9_]{3,29}$'",
            name="user_name_format",  # naming_convention 会前缀 ck_users_
        ),
        CheckConstraint(
            "authority IN ('Administrators','GroupCompany','Company','User')",
            name="authority",  # naming_convention 会前缀 ck_users_
        ),
        UniqueConstraint(
            "user_name", name="user_name"
        ),  # 铁律 2：不用 unique=True，显式 UQ 才能被 naming_convention 正确命名 → uq_users_user_name
        Index("idx_users_tenant", "usr_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    login_name: Mapped[str | None] = mapped_column(String(50))
    group_company: Mapped[str | None] = mapped_column(String(100))
    company: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(100))
    authority: Mapped[str] = mapped_column(String(20), nullable=False)
    control_authority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    sys_name: Mapped[str | None] = mapped_column(String(50))
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group"),
        nullable=False,
    )


class UserWxBinding(Base):
    __tablename__ = "user_wx_bindings"

    openid: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_name: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.user_name", ondelete="CASCADE"),
        nullable=False,
    )
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class UserPhoneNumber(Base):
    __tablename__ = "user_phone_numbers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.user_name", ondelete="CASCADE"), nullable=False
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)


class UserEmail(Base):
    __tablename__ = "user_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(100), nullable=False)
