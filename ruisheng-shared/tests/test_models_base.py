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


def test_naming_convention_registered() -> None:
    """约束命名模板必须注入 metadata，Stage D 的 Alembic 依赖它。"""
    nc = Base.metadata.naming_convention
    assert nc["pk"] == "pk_%(table_name)s"
    assert nc["fk"] == "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    assert set(nc.keys()) == {"ix", "uq", "ck", "fk", "pk"}
