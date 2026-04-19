import fakeredis.aioredis
import pytest
from ruisheng_api.core.login_limit import (
    clear_login_fail,
    is_user_locked,
    record_login_fail,
)


@pytest.mark.asyncio
async def test_record_increments_and_locks_after_threshold():
    r = fakeredis.aioredis.FakeRedis()
    for _ in range(4):
        assert (
            await record_login_fail(
                r,
                "alice",
                "1.1.1.1",
                user_max=5,
                ip_max=20,
                window=300,
                lock_ttl=1800,
                ip_block_ttl=3600,
            )
        ) is False
    # 5th triggers lock
    assert (
        await record_login_fail(
            r,
            "alice",
            "1.1.1.1",
            user_max=5,
            ip_max=20,
            window=300,
            lock_ttl=1800,
            ip_block_ttl=3600,
        )
    ) is True
    assert await is_user_locked(r, "alice") is True


@pytest.mark.asyncio
async def test_clear_resets_counter():
    r = fakeredis.aioredis.FakeRedis()
    await record_login_fail(
        r, "a", "1.1.1.1", user_max=5, ip_max=20, window=300, lock_ttl=1800, ip_block_ttl=3600
    )
    await clear_login_fail(r, "a")
    assert await is_user_locked(r, "a") is False


@pytest.mark.asyncio
async def test_ip_block_after_threshold():
    r = fakeredis.aioredis.FakeRedis()
    for i in range(20):
        await record_login_fail(
            r,
            f"u{i}",
            "9.9.9.9",
            user_max=5,
            ip_max=20,
            window=300,
            lock_ttl=1800,
            ip_block_ttl=3600,
        )
    assert await r.exists("ip_block:9.9.9.9")
