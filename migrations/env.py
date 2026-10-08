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
    eng = sa.create_engine(db.url())
    with eng.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    eng.dispose()


def run_migrations_offline():
    context.configure(url=db.url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
