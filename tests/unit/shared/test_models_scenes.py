"""Spec §4.2 scenes: scene_pages / scene_views (v1.3.4)."""

from ruisheng_shared.models.scenes import ScenePage, SceneView
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex


# ---------------------------------------------------------------------------
# scene_pages
# ---------------------------------------------------------------------------
def test_scene_pages_tablename() -> None:
    assert ScenePage.__tablename__ == "scene_pages"


def test_scene_pages_primary_key() -> None:
    pk = [c.name for c in ScenePage.__table__.primary_key.columns]
    assert pk == ["id"]


def test_scene_pages_columns() -> None:
    cols = {c.name for c in ScenePage.__table__.columns}
    assert cols >= {
        "id",
        "owner_user_name",
        "page_name",
        "sonpage_name",
        "sonpage_pic",
        "pos_x",
        "pos_y",
        "radius",
        "usr_group",
        "deleted_at",
        "created_at",
        "updated_at",
    }


def test_scene_pages_string_lengths() -> None:
    t = ScenePage.__table__
    assert t.columns["owner_user_name"].type.length == 50
    assert t.columns["page_name"].type.length == 100
    assert t.columns["sonpage_name"].type.length == 100
    assert t.columns["sonpage_pic"].type.length == 500
    assert t.columns["usr_group"].type.length == 50


def test_scene_pages_page_name_collation_zh_x_icu() -> None:
    col = ScenePage.__table__.columns["page_name"]
    assert col.type.collation == "zh-x-icu"


def test_scene_pages_sonpage_name_collation_zh_x_icu() -> None:
    col = ScenePage.__table__.columns["sonpage_name"]
    assert col.type.collation == "zh-x-icu"


def test_scene_pages_numeric_precision() -> None:
    t = ScenePage.__table__
    assert t.columns["pos_x"].type.precision == 10
    assert t.columns["pos_x"].type.scale == 2
    assert t.columns["pos_y"].type.precision == 10
    assert t.columns["pos_y"].type.scale == 2
    assert t.columns["radius"].type.precision == 8
    assert t.columns["radius"].type.scale == 2


def test_scene_pages_nullability() -> None:
    t = ScenePage.__table__
    # NOT NULL
    assert t.columns["owner_user_name"].nullable is False
    assert t.columns["page_name"].nullable is False
    assert t.columns["pos_x"].nullable is False
    assert t.columns["pos_y"].nullable is False
    assert t.columns["radius"].nullable is False
    assert t.columns["usr_group"].nullable is False
    # NULLable
    assert t.columns["sonpage_name"].nullable is True
    assert t.columns["sonpage_pic"].nullable is True
    assert t.columns["deleted_at"].nullable is True


def test_scene_pages_owner_user_name_fk_restrict() -> None:
    col = ScenePage.__table__.columns["owner_user_name"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "users"
    assert fks[0].column.name == "user_name"
    assert fks[0].ondelete == "RESTRICT"


def test_scene_pages_usr_group_fk_restrict() -> None:
    col = ScenePage.__table__.columns["usr_group"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "wx_groups"
    assert fks[0].column.name == "usr_group"
    assert fks[0].ondelete == "RESTRICT"


def test_scene_pages_ck_radius() -> None:
    names = {c.name for c in ScenePage.__table__.constraints if c.name}
    assert "ck_scene_pages_radius" in names


def test_scene_pages_ck_pos_x() -> None:
    names = {c.name for c in ScenePage.__table__.constraints if c.name}
    assert "ck_scene_pages_pos_x" in names


def test_scene_pages_ck_pos_y() -> None:
    names = {c.name for c in ScenePage.__table__.constraints if c.name}
    assert "ck_scene_pages_pos_y" in names


def test_scene_pages_index_usr_group() -> None:
    idx_names = {ix.name for ix in ScenePage.__table__.indexes}
    assert "ix_scene_pages_usr_group" in idx_names


def test_scene_pages_index_owner() -> None:
    idx_names = {ix.name for ix in ScenePage.__table__.indexes}
    assert "ix_scene_pages_owner" in idx_names


def test_scene_pages_unique_partial_owner_page_name() -> None:
    idx = next(
        ix for ix in ScenePage.__table__.indexes if ix.name == "ux_scene_pages_owner_page_name"
    )
    assert idx.unique is True
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in ddl
    assert "WHERE" in ddl
    assert "deleted_at IS NULL" in ddl
    cols = [c.name for c in idx.columns]
    assert cols == ["usr_group", "owner_user_name", "page_name"]


def test_scene_pages_has_timestamp_and_soft_delete() -> None:
    cols = {c.name for c in ScenePage.__table__.columns}
    assert "created_at" in cols
    assert "updated_at" in cols
    assert "deleted_at" in cols


# ---------------------------------------------------------------------------
# scene_views
# ---------------------------------------------------------------------------
def test_scene_views_tablename() -> None:
    assert SceneView.__tablename__ == "scene_views"


def test_scene_views_primary_key() -> None:
    pk = [c.name for c in SceneView.__table__.primary_key.columns]
    assert pk == ["id"]


def test_scene_views_columns() -> None:
    cols = {c.name for c in SceneView.__table__.columns}
    assert cols >= {
        "id",
        "scene_page_id",
        "owner_user_name",
        "company",
        "department",
        "dev_number",
        "pos_x",
        "pos_y",
        "radius",
        "usr_group",
        "deleted_at",
        "created_at",
        "updated_at",
    }


def test_scene_views_string_lengths() -> None:
    t = SceneView.__table__
    assert t.columns["owner_user_name"].type.length == 50
    assert t.columns["company"].type.length == 100
    assert t.columns["department"].type.length == 100
    assert t.columns["dev_number"].type.length == 50
    assert t.columns["usr_group"].type.length == 50


def test_scene_views_numeric_precision() -> None:
    t = SceneView.__table__
    assert t.columns["pos_x"].type.precision == 10
    assert t.columns["pos_x"].type.scale == 2
    assert t.columns["pos_y"].type.precision == 10
    assert t.columns["pos_y"].type.scale == 2
    assert t.columns["radius"].type.precision == 8
    assert t.columns["radius"].type.scale == 2


def test_scene_views_nullability() -> None:
    t = SceneView.__table__
    # NOT NULL
    assert t.columns["scene_page_id"].nullable is False
    assert t.columns["owner_user_name"].nullable is False
    assert t.columns["dev_number"].nullable is False
    assert t.columns["pos_x"].nullable is False
    assert t.columns["pos_y"].nullable is False
    assert t.columns["radius"].nullable is False
    assert t.columns["usr_group"].nullable is False
    # NULLable（company/department 展示快照，见 §3.8.18；deleted_at 软删）
    assert t.columns["company"].nullable is True
    assert t.columns["department"].nullable is True
    assert t.columns["deleted_at"].nullable is True


def test_scene_views_scene_page_id_fk_restrict() -> None:
    col = SceneView.__table__.columns["scene_page_id"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "scene_pages"
    assert fks[0].column.name == "id"
    assert fks[0].ondelete == "RESTRICT"


def test_scene_views_owner_user_name_fk_restrict() -> None:
    col = SceneView.__table__.columns["owner_user_name"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "users"
    assert fks[0].column.name == "user_name"
    assert fks[0].ondelete == "RESTRICT"


def test_scene_views_dev_number_fk_restrict() -> None:
    col = SceneView.__table__.columns["dev_number"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "devices"
    assert fks[0].column.name == "dev_number"
    assert fks[0].ondelete == "RESTRICT"


def test_scene_views_usr_group_fk_restrict() -> None:
    col = SceneView.__table__.columns["usr_group"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "wx_groups"
    assert fks[0].column.name == "usr_group"
    assert fks[0].ondelete == "RESTRICT"


def test_scene_views_ck_radius() -> None:
    names = {c.name for c in SceneView.__table__.constraints if c.name}
    assert "ck_scene_views_radius" in names


def test_scene_views_ck_pos_x() -> None:
    names = {c.name for c in SceneView.__table__.constraints if c.name}
    assert "ck_scene_views_pos_x" in names


def test_scene_views_ck_pos_y() -> None:
    names = {c.name for c in SceneView.__table__.constraints if c.name}
    assert "ck_scene_views_pos_y" in names


def test_scene_views_index_usr_group() -> None:
    idx_names = {ix.name for ix in SceneView.__table__.indexes}
    assert "ix_scene_views_usr_group" in idx_names


def test_scene_views_index_page_id() -> None:
    idx_names = {ix.name for ix in SceneView.__table__.indexes}
    assert "ix_scene_views_page_id" in idx_names


def test_scene_views_index_dev_number() -> None:
    idx_names = {ix.name for ix in SceneView.__table__.indexes}
    assert "ix_scene_views_dev_number" in idx_names


def test_scene_views_index_owner() -> None:
    idx_names = {ix.name for ix in SceneView.__table__.indexes}
    assert "ix_scene_views_owner" in idx_names


def test_scene_views_unique_partial_page_dev() -> None:
    idx = next(ix for ix in SceneView.__table__.indexes if ix.name == "ux_scene_views_page_dev")
    assert idx.unique is True
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in ddl
    assert "WHERE" in ddl
    assert "deleted_at IS NULL" in ddl
    cols = [c.name for c in idx.columns]
    assert cols == ["scene_page_id", "dev_number"]


def test_scene_views_has_timestamp_and_soft_delete() -> None:
    cols = {c.name for c in SceneView.__table__.columns}
    assert "created_at" in cols
    assert "updated_at" in cols
    assert "deleted_at" in cols


def test_scene_views_company_department_nullable_snapshot() -> None:
    """展示快照语义（§3.8.18）：API 未传时由 trg_scene_views_fill_snapshot
    从 users(owner_user_name).company/department 自动填充；故列级可为 NULL。"""
    t = SceneView.__table__
    assert t.columns["company"].nullable is True
    assert t.columns["department"].nullable is True


# ---------------------------------------------------------------------------
# Package-level re-export
# ---------------------------------------------------------------------------
def test_models_reexport() -> None:
    from ruisheng_shared import models as m

    assert m.ScenePage is ScenePage
    assert m.SceneView is SceneView
