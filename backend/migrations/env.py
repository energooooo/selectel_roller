from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
DATABASE_URL = get_settings().database_url


def _configure(connection=None) -> None:  # noqa: ANN001
    kwargs = dict(
        target_metadata=target_metadata,
        # SQLite не умеет ALTER COLUMN — batch-режим пересоздаёт таблицу.
        render_as_batch=DATABASE_URL.startswith("sqlite"),
        compare_type=True,
    )
    if connection is None:
        context.configure(url=DATABASE_URL, literal_binds=True, **kwargs)
    else:
        context.configure(connection=connection, **kwargs)


def run_migrations_offline() -> None:
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection) -> None:  # noqa: ANN001
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
