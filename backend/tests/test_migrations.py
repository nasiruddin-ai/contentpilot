from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.core.database import Base
from tests.conftest import alembic_config


def test_migrations_match_models(db_engine):
    """Fails if a model changed without a new migration."""
    with db_engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_and_upgrade_round_trip(db_engine):
    # Postgres DDL is transactional, so this is rolled back afterwards.
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            config = alembic_config(conn)
            command.downgrade(config, "base")
            assert {"users", "brands"}.isdisjoint(inspect(conn).get_table_names())

            command.upgrade(config, "head")
            assert {"users", "brands"} <= set(inspect(conn).get_table_names())
        finally:
            trans.rollback()
