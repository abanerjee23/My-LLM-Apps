from __future__ import annotations

from alembic import context

from sourcelens.config import get_settings
from sourcelens.db import create_database_engine


def run_migrations_online() -> None:
    engine = create_database_engine(get_settings())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
