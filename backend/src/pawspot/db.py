from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from pawspot.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_postgis_version(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text("SELECT PostGIS_Version()")).scalar_one())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
