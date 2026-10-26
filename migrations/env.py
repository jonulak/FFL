from logging.config import fileConfig

from alembic import context
import sqlalchemy as sa

from ffl import db

config = context.config

# Keep the app's loggers (and pytest's capture) alive when a test runs the upgrade in-process.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = db.metadata


def run_migrations_online():
    eng = sa.create_engine(db.url(), json_serializer=db._json_dumps)  # a NaN in an imported file: null, as the app
    with eng.connect() as connection:
        # one transaction for the whole upgrade, DDL included (Postgres DDL is transactional): a migration that
        # fails part way (a bad file in an import) leaves the DB at the revision it started from, ready to re-run
        context.configure(connection=connection, target_metadata=target_metadata, transaction_per_migration=False)
        with context.begin_transaction():
            context.run_migrations()
    eng.dispose()


def run_migrations_offline():
    context.configure(url=db.url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
