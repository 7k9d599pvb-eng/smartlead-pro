"""Alembic runs without a bot token and keeps the database secret out of config."""

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from smartlead.db.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
project_root = Path(__file__).resolve().parent.parent
database_url = os.getenv("DATABASE_URL") or dotenv_values(project_root / ".env").get("DATABASE_URL")
if not database_url:
    raise RuntimeError("Set DATABASE_URL before running Alembic migrations.")
if make_url(database_url).drivername != "postgresql+asyncpg":
    raise RuntimeError("DATABASE_URL must start with postgresql+asyncpg://.")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_sync_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
