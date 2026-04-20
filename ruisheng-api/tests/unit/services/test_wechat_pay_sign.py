from ruisheng_api.services.wechat_pay import verify_sign, wechat_pay_sign


def test_sign_stable_order_independent():
    k = "secret"
    a = wechat_pay_sign({"b": 1, "a": 2}, k)
    b = wechat_pay_sign({"a": 2, "b": 1}, k)
    assert a == b


def test_verify_accepts_case_insensitive():
    k = "secret"
    params = {"a": "1", "b": "2"}
    sig = wechat_pay_sign(params, k)
    assert verify_sign({**params, "sign": sig.lower()}, sig.lower(), k)


def test_verify_rejects_tamper():
    k = "secret"
    params = {"a": "1"}
    sig = wechat_pay_sign(params, k)
    assert not verify_sign({**params, "a": "2", "sign": sig}, sig, k)
