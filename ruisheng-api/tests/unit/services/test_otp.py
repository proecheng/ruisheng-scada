import fakeredis.aioredis
import pytest
from ruisheng_api.services.otp import generate_otp, issue_otp, verify_otp


def test_generate_otp_6_digits():
    for _ in range(20):
        c = generate_otp()
        assert len(c) == 6 and c.isdigit()


@pytest.mark.asyncio
async def test_issue_and_verify_otp():
    r = fakeredis.aioredis.FakeRedis()
    code = await issue_otp(r, action="register", key="138", ttl_sec=300)
    assert await verify_otp(r, action="register", key="138", code=code) is True


@pytest.mark.asyncio
async def test_verify_consumes():
    r = fakeredis.aioredis.FakeRedis()
    code = await issue_otp(r, action="register", key="138", ttl_sec=300)
    assert await verify_otp(r, action="register", key="138", code=code) is True
    assert await verify_otp(r, action="register", key="138", code=code) is False


@pytest.mark.asyncio
async def test_verify_wrong_code():
    r = fakeredis.aioredis.FakeRedis()
    await issue_otp(r, action="register", key="138", ttl_sec=300)
    assert await verify_otp(r, action="register", key="138", code="000000") is False
