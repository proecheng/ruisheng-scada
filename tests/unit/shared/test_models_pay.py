"""Spec §4.2 pay_orders / pay_orders_seen + §5.1 PAY_* ErrCode (v1.3.5)."""

from __future__ import annotations

from ruisheng_shared.models.pay import PayOrder, PayOrderSeen

# ---------------------------------------------------------------------------
# PayOrder — 表名与 PK
# ---------------------------------------------------------------------------


def test_pay_order_tablename() -> None:
    assert PayOrder.__tablename__ == "pay_orders"


def test_pay_order_pk_is_out_trade_no() -> None:
    pk = [c.name for c in PayOrder.__table__.primary_key.columns]
    assert pk == ["out_trade_no"]


def test_pay_order_no_id_column() -> None:
    """§4.6：out_trade_no 直接作 PK，不应有 id 列。"""
    cols = {c.name for c in PayOrder.__table__.columns}
    assert "id" not in cols


# ---------------------------------------------------------------------------
# PayOrder — 列类型与 nullable
# ---------------------------------------------------------------------------


def test_pay_order_out_trade_no_string() -> None:
    from sqlalchemy import String

    col = PayOrder.__table__.columns["out_trade_no"]
    assert isinstance(col.type, String)
    assert col.primary_key is True


def test_pay_order_openid_string100_not_null() -> None:
    from sqlalchemy import String

    col = PayOrder.__table__.columns["openid"]
    assert isinstance(col.type, String)
    assert col.type.length == 100
    assert col.nullable is False


def test_pay_order_usr_group_string50_not_null() -> None:
    from sqlalchemy import String

    col = PayOrder.__table__.columns["usr_group"]
    assert isinstance(col.type, String)
    assert col.type.length == 50
    assert col.nullable is False


def test_pay_order_total_fee_integer_not_null() -> None:
    from sqlalchemy import Integer

    col = PayOrder.__table__.columns["total_fee"]
    assert isinstance(col.type, Integer)
    assert col.nullable is False


def test_pay_order_body_string255_nullable() -> None:
    from sqlalchemy import String

    col = PayOrder.__table__.columns["body"]
    assert isinstance(col.type, String)
    assert col.type.length == 255
    assert col.nullable is True


def test_pay_order_pay_state_string20_not_null() -> None:
    from sqlalchemy import String

    col = PayOrder.__table__.columns["pay_state"]
    assert isinstance(col.type, String)
    assert col.type.length == 20
    assert col.nullable is False


def test_pay_order_created_at_datetime_not_null() -> None:
    from sqlalchemy import DateTime

    col = PayOrder.__table__.columns["created_at"]
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True
    assert col.nullable is False


def test_pay_order_updated_at_datetime_not_null() -> None:
    from sqlalchemy import DateTime

    col = PayOrder.__table__.columns["updated_at"]
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True
    assert col.nullable is False


def test_pay_order_paid_at_nullable() -> None:
    from sqlalchemy import DateTime

    col = PayOrder.__table__.columns["paid_at"]
    assert isinstance(col.type, DateTime)
    assert col.nullable is True


def test_pay_order_refund_at_nullable() -> None:
    from sqlalchemy import DateTime

    col = PayOrder.__table__.columns["refund_at"]
    assert isinstance(col.type, DateTime)
    assert col.nullable is True


def test_pay_order_deleted_at_nullable() -> None:
    from sqlalchemy import DateTime

    col = PayOrder.__table__.columns["deleted_at"]
    assert isinstance(col.type, DateTime)
    assert col.nullable is True


# ---------------------------------------------------------------------------
# PayOrder — FK
# ---------------------------------------------------------------------------


def test_pay_order_usr_group_fk_wx_groups() -> None:
    col = PayOrder.__table__.columns["usr_group"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "wx_groups"
    assert fk.column.name == "usr_group"
    assert fk.ondelete == "RESTRICT"


# ---------------------------------------------------------------------------
# PayOrder — CHECK 约束
# ---------------------------------------------------------------------------


def test_pay_order_ck_total_fee_nonneg() -> None:
    """total_fee >= 0"""
    ck_texts = {
        str(c.sqltext).lower() for c in PayOrder.__table__.constraints if hasattr(c, "sqltext")
    }
    assert any("total_fee" in t and "0" in t for t in ck_texts)


def test_pay_order_ck_pay_state_enum() -> None:
    """pay_state IN 6 values including 'cancelled' and 'expired'."""
    ck_texts = {str(c.sqltext) for c in PayOrder.__table__.constraints if hasattr(c, "sqltext")}
    enum_ck = next((t for t in ck_texts if "cancelled" in t and "expired" in t), None)
    assert enum_ck is not None, f"No pay_state enum CK found in: {ck_texts}"
    for val in ("pending", "paid", "failed", "refund", "cancelled", "expired"):
        assert val in enum_ck


def test_pay_order_ck_paid_biconditional() -> None:
    """(pay_state = 'paid') = (paid_at IS NOT NULL)"""
    ck_texts = {str(c.sqltext) for c in PayOrder.__table__.constraints if hasattr(c, "sqltext")}
    biconditional = next((t for t in ck_texts if "paid_at" in t and "pay_state" in t), None)
    assert biconditional is not None, f"No paid biconditional CK found in: {ck_texts}"


def test_pay_order_ck_refund_at_implies_paid_at() -> None:
    """refund_at IS NULL OR paid_at IS NOT NULL (退款必先于支付)"""
    ck_texts = {str(c.sqltext) for c in PayOrder.__table__.constraints if hasattr(c, "sqltext")}
    refund_cks = [t for t in ck_texts if "refund_at" in t]
    assert len(refund_cks) >= 1, f"No refund_at CK found in: {ck_texts}"


def test_pay_order_ck_refund_at_ge_paid_at() -> None:
    """refund_at IS NULL OR refund_at >= paid_at"""
    ck_texts = {str(c.sqltext) for c in PayOrder.__table__.constraints if hasattr(c, "sqltext")}
    # At least one refund CK mentioning >=
    time_ck = next(
        (t for t in ck_texts if "refund_at" in t and ">=" in t),
        None,
    )
    assert time_ck is not None, f"No refund_at >= paid_at CK found in: {ck_texts}"


# ---------------------------------------------------------------------------
# PayOrder — DEFAULT pay_state
# ---------------------------------------------------------------------------


def test_pay_order_pay_state_default_pending() -> None:
    col = PayOrder.__table__.columns["pay_state"]
    # server_default or default should contain 'pending'
    default_val = col.server_default or col.default
    assert default_val is not None
    default_str = str(default_val.arg) if hasattr(default_val, "arg") else str(default_val)
    assert "pending" in default_str


# ---------------------------------------------------------------------------
# PayOrder — Indexes (partial)
# ---------------------------------------------------------------------------


def test_pay_order_index_usr_group_created_partial() -> None:
    """ix_pay_orders_usr_group_created WHERE deleted_at IS NULL"""
    idx = next(
        (ix for ix in PayOrder.__table__.indexes if ix.name == "ix_pay_orders_usr_group_created"),
        None,
    )
    assert idx is not None, "ix_pay_orders_usr_group_created not found"
    where_clause = idx.dialect_kwargs.get("postgresql_where")
    assert where_clause is not None
    assert "deleted_at" in str(where_clause).lower()


def test_pay_order_index_usr_group_created_columns() -> None:
    idx = next(
        ix for ix in PayOrder.__table__.indexes if ix.name == "ix_pay_orders_usr_group_created"
    )
    assert any("usr_group" in str(c) for c in idx.expressions)
    assert any("created_at" in str(c) for c in idx.expressions)


def test_pay_order_index_openid_created_partial() -> None:
    """ix_pay_orders_openid_created WHERE deleted_at IS NULL"""
    idx = next(
        (ix for ix in PayOrder.__table__.indexes if ix.name == "ix_pay_orders_openid_created"),
        None,
    )
    assert idx is not None, "ix_pay_orders_openid_created not found"
    where_clause = idx.dialect_kwargs.get("postgresql_where")
    assert where_clause is not None
    assert "deleted_at" in str(where_clause).lower()


def test_pay_order_index_pending_created_partial() -> None:
    """ix_pay_orders_pending_created WHERE pay_state = 'pending' AND deleted_at IS NULL"""
    idx = next(
        (ix for ix in PayOrder.__table__.indexes if ix.name == "ix_pay_orders_pending_created"),
        None,
    )
    assert idx is not None, "ix_pay_orders_pending_created not found"
    where_clause = idx.dialect_kwargs.get("postgresql_where")
    assert where_clause is not None
    where_str = str(where_clause).lower()
    assert "pending" in where_str
    assert "deleted_at" in where_str


# ---------------------------------------------------------------------------
# PayOrder — Mixin 继承
# ---------------------------------------------------------------------------


def test_pay_order_inherits_timestamp_mixin() -> None:
    from ruisheng_shared.models.base import TimestampMixin

    assert isinstance(PayOrder(), TimestampMixin)


def test_pay_order_inherits_soft_delete_mixin() -> None:
    from ruisheng_shared.models.base import SoftDeleteMixin

    assert isinstance(PayOrder(), SoftDeleteMixin)


# ---------------------------------------------------------------------------
# PayOrderSeen — 基础
# ---------------------------------------------------------------------------


def test_pay_order_seen_tablename() -> None:
    assert PayOrderSeen.__tablename__ == "pay_orders_seen"


def test_pay_order_seen_pk_is_out_trade_no() -> None:
    pk = [c.name for c in PayOrderSeen.__table__.primary_key.columns]
    assert pk == ["out_trade_no"]


def test_pay_order_seen_out_trade_no_type() -> None:
    from sqlalchemy import String

    col = PayOrderSeen.__table__.columns["out_trade_no"]
    assert isinstance(col.type, String)
    assert col.primary_key is True


def test_pay_order_seen_notified_at_not_null() -> None:
    from sqlalchemy import DateTime

    col = PayOrderSeen.__table__.columns["notified_at"]
    assert isinstance(col.type, DateTime)
    assert col.nullable is False


def test_pay_order_seen_no_deleted_at() -> None:
    cols = {c.name for c in PayOrderSeen.__table__.columns}
    assert "deleted_at" not in cols


def test_pay_order_seen_no_updated_at() -> None:
    cols = {c.name for c in PayOrderSeen.__table__.columns}
    assert "updated_at" not in cols


def test_pay_order_seen_no_usr_group() -> None:
    cols = {c.name for c in PayOrderSeen.__table__.columns}
    assert "usr_group" not in cols


def test_pay_order_seen_not_mixin_subclass() -> None:
    from ruisheng_shared.models.base import SoftDeleteMixin, TimestampMixin

    assert not isinstance(PayOrderSeen(), TimestampMixin)
    assert not isinstance(PayOrderSeen(), SoftDeleteMixin)


def test_pay_order_seen_brin_index() -> None:
    """ix_pay_orders_seen_notified_at_brin — BRIN index on notified_at."""
    idx = next(
        (
            ix
            for ix in PayOrderSeen.__table__.indexes
            if ix.name == "ix_pay_orders_seen_notified_at_brin"
        ),
        None,
    )
    assert idx is not None, "ix_pay_orders_seen_notified_at_brin not found"
    using = idx.dialect_kwargs.get("postgresql_using")
    assert using == "brin", f"Expected brin, got: {using}"


# ---------------------------------------------------------------------------
# ErrCode PAY_* (-400 series)
# ---------------------------------------------------------------------------


def test_errcode_pay_sign_fail() -> None:
    from ruisheng_shared.errors import ErrCode

    assert ErrCode.PAY_SIGN_FAIL == -400


def test_errcode_pay_duplicate() -> None:
    from ruisheng_shared.errors import ErrCode

    assert ErrCode.PAY_DUPLICATE == -401


def test_errcode_pay_state_conflict() -> None:
    from ruisheng_shared.errors import ErrCode

    assert ErrCode.PAY_STATE_CONFLICT == -402


def test_errcode_pay_amount_mismatch() -> None:
    from ruisheng_shared.errors import ErrCode

    assert ErrCode.PAY_AMOUNT_MISMATCH == -403


def test_errcode_pay_expired() -> None:
    from ruisheng_shared.errors import ErrCode

    assert ErrCode.PAY_EXPIRED == -404


def test_errcode_pay_refund_fail() -> None:
    from ruisheng_shared.errors import ErrCode

    assert ErrCode.PAY_REFUND_FAIL == -405
