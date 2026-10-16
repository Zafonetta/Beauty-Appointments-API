from logging.config import fileConfig
import sys
from pathlib import Path
import asyncio
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Add project root to sys.path so Alembic finds database.py and models.py
sys.path.append(str(Path(__file__).parent.parent))

# Import Base, models, and settings
from logging.config import fileConfig
import sys
from pathlib import Path
import asyncio

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine  # Changed from async_engine_from_config
from alembic import context

# Add project root to sys.path so Alembic finds database.py and models.py
sys.path.append(str(Path(__file__).parent.parent))

# Import Base, models, and settings
from database import Base
import models
from config import settings

config = context.config

# Inject your DATABASE_URL from .env into Alembic's config programmatically
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations asynchronously for asyncpg with Neon SSL support."""
    # Use create_async_engine directly to pass connect_args
    connectable = create_async_engine(
        settings.database_url,
        connect_args={"ssl": "require"},
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
