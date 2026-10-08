from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from pawspot.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url, pool_pre_ping=True, hide_parameters=True
    )


def get_postgis_version(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text("SELECT PostGIS_Version()")).scalar_one())


@lru_cache
def get_schema_heads() -> frozenset[str]:
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    return frozenset(ScriptDirectory(migrations).get_heads())


def check_database_readiness(engine: Engine) -> str | None:
    expected = get_schema_heads()
    with engine.connect() as connection:
        postgis_version, schema = connection.execute(
            text("SELECT PostGIS_Version(), current_schema()")
        ).one()
        # Не подхватываем version table другой схемы через search_path fallback.
        quoted_schema = connection.dialect.identifier_preparer.quote_schema(schema)
        revisions = (
            connection.execute(
                text(f"SELECT version_num FROM {quoted_schema}.alembic_version")
            )
            .scalars()
            .all()
        )
    if not expected or frozenset(revisions) != expected:
        return None
    return str(postgis_version)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
