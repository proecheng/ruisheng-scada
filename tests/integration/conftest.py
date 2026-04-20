import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def seed_tenants(gw_engine):
    """D9 最小 tenant 种子：ug_A + ug_B 两个 wx_groups + user_of_ugB 用户。
    gw_engine BYPASSRLS + ON CONFLICT DO NOTHING 幂等；不 teardown（dev 容器整体 reset）。
    fixture 为 function scope（见 tests/conftest.py engine fixture 注释）。
    """
    async with gw_engine.connect() as conn, conn.begin():
        await conn.execute(
            text("""
                INSERT INTO wx_groups (usr_group, company_name)
                VALUES ('ug_A', 'Company A'), ('ug_B', 'Company B')
                ON CONFLICT (usr_group) DO NOTHING
            """)
        )
        # users: user_name / password_hash / authority (VARCHAR enum) /
        #        control_authority (SmallInt) / usr_group
        # authority ∈ {'Administrators','GroupCompany','Company','User'}
        await conn.execute(
            text("""
                INSERT INTO users
                    (user_name, password_hash, authority, control_authority, usr_group)
                VALUES
                    ('user_of_ugB', 'test-not-a-real-hash', 'User', 0, 'ug_B')
                ON CONFLICT (user_name) DO NOTHING
            """)
        )
    yield
