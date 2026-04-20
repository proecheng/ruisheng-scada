import pytest
from ruisheng_api.services.notification.email import EmailNotifier
from ruisheng_api.services.notification.registry import build_notifier, list_providers
from ruisheng_api.services.notification.sms import CustomHttpSmsNotifier
from ruisheng_api.services.notification.wechat import WechatNotifier


def test_build_wechat_notifier():
    n = build_notifier("wechat", {"access_token": "tok", "template_id": "tpl"})
    assert isinstance(n, WechatNotifier)
    assert n.name == "wechat"


def test_build_email_notifier():
    n = build_notifier("email", {"host": "h", "port": 587, "user": "u", "password": "pw"})
    assert isinstance(n, EmailNotifier)


def test_build_sms_notifier():
    n = build_notifier("sms_custom_http", {"endpoint": "https://sms.example.com"})
    assert isinstance(n, CustomHttpSmsNotifier)


def test_build_unknown_raises():
    with pytest.raises(ValueError, match="Unknown"):
        build_notifier("nonexistent", {})


def test_list_providers():
    providers = list_providers()
    assert "wechat" in providers
    assert "email" in providers
    assert "sms_custom_http" in providers
