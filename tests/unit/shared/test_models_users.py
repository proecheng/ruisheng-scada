"""Spec §4.2 users / user_wx_bindings / user_phone_numbers / user_emails"""
from ruisheng_shared.models.users import User, UserEmail, UserPhoneNumber, UserWxBinding


# --- User -----------------------------------------------------------------
def test_users_tablename() -> None:
    assert User.__tablename__ == "users"


def test_users_columns() -> None:
    cols = {c.name for c in User.__table__.columns}
    assert cols >= {
        "id", "user_name", "password_hash", "login_name", "group_company",
        "company", "department", "authority", "control_authority", "sys_name",
        "usr_group", "created_at", "updated_at", "deleted_at",
    }


def test_users_primary_key() -> None:
    pk = [c.name for c in User.__table__.primary_key.columns]
    assert pk == ["id"]


def test_users_check_constraints() -> None:
    names = {c.name for c in User.__table__.constraints if c.name}
    assert "ck_users_user_name_format" in names
    assert "ck_users_authority" in names


def test_users_tenant_index() -> None:
    idx_names = {ix.name for ix in User.__table__.indexes}
    assert "idx_users_tenant" in idx_names


# --- UserWxBinding --------------------------------------------------------
def test_wx_binding_tablename() -> None:
    assert UserWxBinding.__tablename__ == "user_wx_bindings"


def test_wx_binding_pk() -> None:
    pk = [c.name for c in UserWxBinding.__table__.primary_key.columns]
    assert pk == ["openid"]


def test_wx_binding_columns() -> None:
    cols = {c.name for c in UserWxBinding.__table__.columns}
    assert cols >= {"openid", "user_name", "usr_group", "bound_at"}


# --- UserPhoneNumber ------------------------------------------------------
def test_phone_tablename() -> None:
    assert UserPhoneNumber.__tablename__ == "user_phone_numbers"


def test_phone_columns() -> None:
    cols = {c.name for c in UserPhoneNumber.__table__.columns}
    assert cols >= {"id", "user_name", "phone_number"}


# --- UserEmail ------------------------------------------------------------
def test_email_tablename() -> None:
    assert UserEmail.__tablename__ == "user_emails"


def test_email_columns() -> None:
    cols = {c.name for c in UserEmail.__table__.columns}
    assert cols >= {"id", "phone_number", "email"}
