"""Alembic environment.

The database URL comes from the application settings rather than from alembic.ini, so
there is one place that knows where the database is. A migration run against the wrong
database because two config files disagreed is a bad afternoon.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from trajectory_api import tables  # noqa: F401  imported so the metadata is populated
from trajectory_api.settings import get_settings

config = context.config
target_metadata = SQLModel.metadata

config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for review before a production change."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the configured database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
