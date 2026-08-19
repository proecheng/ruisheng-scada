import fakeredis.aioredis
from ruisheng_gw.persistence.repository import Repository, _alarm_matches, _lx_required_count


def test_alarm_comparators() -> None:
    assert _alarm_matches(11.0, ">", 10.0)
    assert _alarm_matches(9.0, "<", 10.0)
    assert _alarm_matches(10.0, "=", 10.0)
    assert _alarm_matches(11.0, "!=", 10.0)


def test_unknown_alarm_comparator_is_safe() -> None:
    assert not _alarm_matches(11.0, "LX", 10.0)


async def test_lx_counter_persists_and_normal_value_clears() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    first = Repository(object(), redis=redis)  # type: ignore[arg-type]
    assert not await first._lx_matches(  # noqa: SLF001
        dev_number="D1", alarm_cfg_id=7, value=1.0, required_count=3
    )
    second = Repository(object(), redis=redis)  # type: ignore[arg-type]
    assert not await second._lx_matches(  # noqa: SLF001
        dev_number="D1", alarm_cfg_id=7, value=1.0, required_count=3
    )
    assert await second._lx_matches(  # noqa: SLF001
        dev_number="D1", alarm_cfg_id=7, value=1.0, required_count=3
    )
    assert not await second._lx_matches(  # noqa: SLF001
        dev_number="D1", alarm_cfg_id=7, value=0.0, required_count=3
    )
    assert int(await redis.hget("lx_counter:D1", "7:main") or 0) == 0


async def test_relation_lx_uses_independent_counter() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    repository = Repository(object(), redis=redis)  # type: ignore[arg-type]
    cfg = {
        "id": 7,
        "dev_number": "D1",
        "alarm_type": ">",
        "limit_value": 10.0,
        "relation_point_id": 2,
        "relation_alarm_type": "LX",
        "relation_limit_value": 2.0,
    }
    assert not await repository._alarm_condition(  # noqa: SLF001
        cfg=cfg, value=11.0, relation_value=1.0
    )
    assert await repository._alarm_condition(  # noqa: SLF001
        cfg=cfg, value=11.0, relation_value=1.0
    )
    assert await redis.hget("lx_counter:D1", "7:relation") == b"2"


async def test_missing_relation_is_unknown_and_keeps_counters() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    repository = Repository(object(), redis=redis)  # type: ignore[arg-type]
    await redis.hset("lx_counter:D1", "7:main", 2)
    cfg = {
        "id": 7,
        "dev_number": "D1",
        "alarm_type": ">",
        "limit_value": 10.0,
        "relation_point_id": 2,
        "relation_alarm_type": ">",
        "relation_limit_value": 5.0,
    }
    assert (
        await repository._alarm_condition(cfg=cfg, value=11.0, relation_value=None)  # noqa: SLF001
        is None
    )
    assert await redis.hget("lx_counter:D1", "7:main") == b"2"


async def test_config_reload_clears_all_lx_counters_for_affected_devices() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    repository = Repository(object(), redis=redis)  # type: ignore[arg-type]
    await redis.hset("lx_counter:D1", mapping={"7:main": 2, "7:relation": 1})
    await redis.hset("lx_counter:D2", "8:main", 1)

    await repository.reset_lx_counters_for_devices({"D1"})

    assert not await redis.exists("lx_counter:D1")
    assert await redis.exists("lx_counter:D2")


async def test_lx_counters_are_isolated_by_config_version() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    repository = Repository(object(), redis=redis)  # type: ignore[arg-type]

    assert not await repository._lx_matches(  # noqa: SLF001
        dev_number="D1",
        alarm_cfg_id=7,
        value=1.0,
        required_count=2,
        config_version=1,
    )
    assert not await repository._lx_matches(  # noqa: SLF001
        dev_number="D1",
        alarm_cfg_id=7,
        value=1.0,
        required_count=2,
        config_version=2,
    )
    assert await repository._lx_matches(  # noqa: SLF001
        dev_number="D1",
        alarm_cfg_id=7,
        value=1.0,
        required_count=2,
        config_version=2,
    )
    assert await redis.hget("lx_counter:D1:1", "7:main") == b"1"
    assert await redis.hget("lx_counter:D1:2", "7:main") == b"2"


def test_lx_count_rejects_fractional_and_non_positive_values() -> None:
    assert _lx_required_count(3.0) == 3
    assert _lx_required_count(0) is None
    assert _lx_required_count(-1) is None
    assert _lx_required_count(1.5) is None


async def test_empty_outbox_still_pings_redis() -> None:
    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Conn:
        async def execute(self, *args, **kwargs):
            return Result()

    class ConnectionContext:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *args):
            return None

    class Engine:
        def connect(self):
            return ConnectionContext()

    class Redis:
        def __init__(self) -> None:
            self.pings = 0

        async def ping(self):
            self.pings += 1
            return True

    redis = Redis()
    repository = Repository(Engine())  # type: ignore[arg-type]
    assert await repository.relay_alarm_outbox_once(redis) == 0
    assert redis.pings == 1
