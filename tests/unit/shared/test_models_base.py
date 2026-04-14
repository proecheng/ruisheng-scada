"""Base / mixin 可被子类继承。"""

from __future__ import annotations

from ruisheng_shared.models.base import Base, TimestampMixin
from sqlalchemy.orm import Mapped, mapped_column


class _Sample(Base, TimestampMixin):
    __tablename__ = "_sample"
    id: Mapped[int] = mapped_column(primary_key=True)


def test_subclass_has_created_updated() -> None:
    assert hasattr(_Sample, "created_at")
    assert hasattr(_Sample, "updated_at")


def test_tablename_snake_case() -> None:
    assert _Sample.__tablename__ == "_sample"
