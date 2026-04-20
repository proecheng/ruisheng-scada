import pytest
from ruisheng_api.core.rbac import CurrentUser, check_ca, check_role
from ruisheng_shared.errors.codes import BizError, ErrCode


def _user(role="User", ca=0):
    return CurrentUser(
        user_name="alice",
        usr_group="g1",
        role=role,
        control_authority=ca,
        jti="01HXY",
        fp="fp1",
    )


def test_check_role_pass():
    check_role(_user(role="Company"), allowed=("Company", "Administrators"))


def test_check_role_fail():
    with pytest.raises(BizError) as ei:
        check_role(_user(role="User"), allowed=("Company", "Administrators"))
    assert ei.value.code == ErrCode.FORBIDDEN


def test_check_ca_pass_bit0():
    check_ca(_user(ca=0b001), bit=0x01)


def test_check_ca_fail():
    with pytest.raises(BizError) as ei:
        check_ca(_user(ca=0b000), bit=0x01)
    assert ei.value.code == ErrCode.FORBIDDEN
