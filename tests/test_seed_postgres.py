"""Startup seed jobs preserve an operator's customized catalog and business settings."""

import asyncio

import pytest
from sqlalchemy import func, select

from smartlead.config import Settings
from smartlead.db.models import BusinessSettings, Service
from smartlead.db.seed import seed_database

pytestmark = pytest.mark.integration


async def test_concurrent_and_repeated_seed_preserves_customization(
    clean_pg,
    migrated_test_database_url,
):
    settings = Settings(
        _env_file=None,
        bot_token="123456:seed-test-token",
        database_url=migrated_test_database_url,
        business_name="Configured Studio",
    )
    await asyncio.gather(seed_database(clean_pg, settings), seed_database(clean_pg, settings))
    async with clean_pg.begin() as session:
        assert await session.scalar(select(func.count(Service.id))) == 2
        service = await session.scalar(select(Service).order_by(Service.id))
        service.name = "Personal consultation"
        business = await session.get(BusinessSettings, 1)
        business.name = "Saved Studio"

    await seed_database(clean_pg, settings)
    async with clean_pg() as session:
        assert await session.scalar(select(func.count(Service.id))) == 2
        assert (
            await session.scalar(select(Service.name).order_by(Service.id))
            == "Personal consultation"
        )
        assert (await session.get(BusinessSettings, 1)).name == "Saved Studio"

    await seed_database(clean_pg, settings, sync_settings=True)
    async with clean_pg() as session:
        assert (await session.get(BusinessSettings, 1)).name == "Configured Studio"
        assert await session.scalar(select(func.count(Service.id))) == 2
