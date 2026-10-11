"""Shared fixtures; database cleanup is gated by a dedicated test database name."""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from alembic import command
from smartlead.db.session import create_engine, create_session_factory


@pytest.fixture(scope="session")
def migrated_test_database_url() -> str:
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL integration tests")

    try:
        url = make_url(configured)
    except Exception as exc:
        pytest.fail(f"TEST_DATABASE_URL is not a valid SQLAlchemy URL: {exc}")
    if url.drivername != "postgresql+asyncpg":
        pytest.fail("TEST_DATABASE_URL must use postgresql+asyncpg")
    if not url.database or "test" not in url.database.lower():
        pytest.fail(
            "refusing integration tests: TEST_DATABASE_URL database name must contain 'test'"
        )

    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "alembic"))
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = configured
    try:
        command.upgrade(alembic_config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    return configured


@pytest_asyncio.fixture
async def pg_engine(migrated_test_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_test_database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine: AsyncEngine) -> async_sessionmaker:
    return create_session_factory(pg_engine)


@pytest_asyncio.fixture
async def clean_pg(pg_engine: AsyncEngine, pg_session_factory: async_sessionmaker):
    """Start a test with empty application tables; name guard runs before reaching here."""
    async with pg_engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE notification_outbox, status_history, appointments, requests, "
                "business_settings, services, users RESTART IDENTITY CASCADE"
            )
        )
    return pg_session_factory
