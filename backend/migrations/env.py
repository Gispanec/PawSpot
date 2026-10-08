from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from pawspot.config import get_settings
from pawspot.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=config.attributes.get("version_table_schema"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if isinstance(supplied_connection, Connection):
        migrate_connection(supplied_connection)
        return
    engine = create_engine(
        get_settings().database_url, poolclass=pool.NullPool, hide_parameters=True
    )
    with engine.connect() as connection:
        migrate_connection(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
