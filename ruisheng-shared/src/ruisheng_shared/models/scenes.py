"""组态相关 2 张表。对应 spec §4.2 v1.3.4（scene_pages / scene_views）。

- ``scene_pages``：组态画面模板。``page_name`` / ``sonpage_name`` 使用 COLLATE
  ``zh-x-icu`` 以支持中文排序；partial UNIQUE ``(usr_group, owner_user_name,
  page_name)`` 仅在 ``deleted_at IS NULL`` 时生效（允许软删后同名重建）。坐标
  ``pos_x`` / ``pos_y`` 为 Numeric(10, 2)，``radius`` 为 Numeric(8, 2)，均有
  sanity bounds CHECK。
- ``scene_views``：画布上"设备热点"绑定。保留 ``company`` / ``department`` 展示
  快照（非 FK，允许 NULL，见 §3.8.18）；partial UNIQUE ``(scene_page_id,
  dev_number)`` 保证同页同设备只配 1 个热点。

**触发器（由 Stage D alembic migration 落地，本 ORM 层不实现）**：

- ``enforce_scene_tenant_consistency()``（spec §4.1.1 (4)）：
  ``BEFORE INSERT OR UPDATE`` 于 scene_pages / scene_views，校验行上 usr_group
  与 users(owner_user_name) / scene_pages(scene_page_id) / devices(dev_number)
  各自的 usr_group 一致，违反抛 ``scene_tenant_violation``。
- ``fill_scene_views_snapshot()``（spec §4.1.1 (5)）：``BEFORE INSERT`` 于
  scene_views，API 层未传 ``company`` / ``department`` 时自动从 ``users`` 读取
  填充（展示快照语义见 §3.8.18）。

**gw 禁止访问**（§3.7）：scene_* 是 UI 配置类数据，gw 实时通路无业务理由读写；
CI lint 扫描 gw 代码库 SQL 字面量禁出现这两张表名，违规 P0 阻塞合并。

**ZTPageInf / ZTViewInf 迁移**（§4.5）：旧表映射非直通，一次性 ETL 导入。

WARN（运行期语义，非 Python 层校验）：

- 父 ``ScenePage`` 软删不级联 ``SceneView``，API 层需批量软删（spec §3.7 禁止
  ``ON DELETE CASCADE``；软删使用 ``deleted_at`` 时间戳而非物理删除）。
- 触发器 ``enforce_scene_tenant_consistency`` 读 ``users.usr_group`` 时不过滤
  ``deleted_at``；owner 用户软删后，已存在的 ``SceneView`` 行仍可 UPDATE 成功。
- Q-B08 TODO（子页面多级树形 / 背景图分辨率限制 / SVG 支持）：当前直通单层
  ``sonpage_name`` + ``sonpage_pic``；多级化需加 ``parent_id`` + 防环 CHECK。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin


class ScenePage(Base, TimestampMixin, SoftDeleteMixin):
    """组态画面模板（spec §4.2 scene_pages，v1.3.4 新增）。"""

    __tablename__ = "scene_pages"
    __table_args__ = (
        CheckConstraint(
            "radius BETWEEN 0.01 AND 100000",
            name="radius",  # → ck_scene_pages_radius
        ),
        CheckConstraint(
            "pos_x BETWEEN -1000000 AND 1000000",
            name="pos_x",  # → ck_scene_pages_pos_x
        ),
        CheckConstraint(
            "pos_y BETWEEN -1000000 AND 1000000",
            name="pos_y",  # → ck_scene_pages_pos_y
        ),
        Index("ix_scene_pages_usr_group", "usr_group"),
        Index("ix_scene_pages_owner", "owner_user_name"),
        # partial UNIQUE：同租户同归属人内 page_name 唯一；软删后同名可重建。
        # SQLAlchemy UniqueConstraint 不支持 WHERE 子句，故用 Index(unique=True)。
        Index(
            "ux_scene_pages_owner_page_name",
            "usr_group",
            "owner_user_name",
            "page_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_user_name: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.user_name", ondelete="RESTRICT"),
        nullable=False,
    )
    page_name: Mapped[str] = mapped_column(String(100, collation="zh-x-icu"), nullable=False)
    sonpage_name: Mapped[str | None] = mapped_column(String(100, collation="zh-x-icu"))
    sonpage_pic: Mapped[str | None] = mapped_column(String(500))
    pos_x: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    pos_y: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    radius: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group", ondelete="RESTRICT"),
        nullable=False,
    )


class SceneView(Base, TimestampMixin, SoftDeleteMixin):
    """画布上设备热点绑定（spec §4.2 scene_views，v1.3.4 新增）。

    ``company`` / ``department`` 为展示快照列（非 FK、允许 NULL），INSERT 时由
    ``fill_scene_views_snapshot()`` 触发器从 users 自动填充，见 §3.8.18。
    """

    __tablename__ = "scene_views"
    __table_args__ = (
        CheckConstraint(
            "radius BETWEEN 0.01 AND 100000",
            name="radius",  # → ck_scene_views_radius
        ),
        CheckConstraint(
            "pos_x BETWEEN -1000000 AND 1000000",
            name="pos_x",  # → ck_scene_views_pos_x
        ),
        CheckConstraint(
            "pos_y BETWEEN -1000000 AND 1000000",
            name="pos_y",  # → ck_scene_views_pos_y
        ),
        Index("ix_scene_views_usr_group", "usr_group"),
        Index("ix_scene_views_page_id", "scene_page_id"),
        Index("ix_scene_views_dev_number", "dev_number"),
        Index("ix_scene_views_owner", "owner_user_name"),
        # partial UNIQUE：同页同设备只配 1 个热点；软删后同组合可重建。
        # 若未来支持同设备多视角，扩展为 (scene_page_id, dev_number, view_kind)
        Index(
            "ux_scene_views_page_dev",
            "scene_page_id",
            "dev_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scene_page_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("scene_pages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_user_name: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.user_name", ondelete="RESTRICT"),
        nullable=False,
    )
    # §3.8.18 展示快照：INSERT 时 trg 自动填充，UPDATE 不自动同步。
    company: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(100))
    dev_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.dev_number", ondelete="RESTRICT"),
        nullable=False,
    )
    pos_x: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    pos_y: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    radius: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group", ondelete="RESTRICT"),
        nullable=False,
    )
