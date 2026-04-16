"""Spec §4.2 wx_groups"""

from ruisheng_shared.models.tenants import WxGroup


def test_tablename() -> None:
    assert WxGroup.__tablename__ == "wx_groups"


def test_columns_exist() -> None:
    cols = {c.name for c in WxGroup.__table__.columns}
    assert cols >= {
        "usr_group",
        "appid",
        "appsecret",
        "token",
        "token_expires_at",
        "template_id",
        "company_name",
        "sys_title",
        "remark",
        "created_at",
        "updated_at",
    }


def test_primary_key() -> None:
    pk = [c.name for c in WxGroup.__table__.primary_key.columns]
    assert pk == ["usr_group"]
