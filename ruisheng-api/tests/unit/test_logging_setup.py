from ruisheng_api.logging_setup import SENSITIVE_KEYS, configure_logging, redact_record


def test_redact_drops_password():
    rec = {"extra": {"password": "secret", "user": "a"}}
    redact_record(rec)
    assert rec["extra"]["password"] == "***"
    assert rec["extra"]["user"] == "a"


def test_redact_phone_masks_middle():
    rec = {"extra": {"phone_number": "13812345678"}}
    redact_record(rec)
    assert rec["extra"]["phone_number"] == "138****5678"


def test_redact_short_phone_fully_hidden():
    rec = {"extra": {"phone_number": "123"}}
    redact_record(rec)
    assert rec["extra"]["phone_number"] == "***"


def test_redact_keeps_other_keys():
    rec = {"extra": {"dev_number": "60270012", "random": 42}}
    redact_record(rec)
    assert rec["extra"]["random"] == 42


def test_configure_logging_idempotent():
    configure_logging(level="INFO")
    configure_logging(level="INFO")  # second call must not raise


def test_all_sensitive_keys_known():
    assert {"password", "secret", "token", "jwt", "authorization"}.issubset(SENSITIVE_KEYS)
