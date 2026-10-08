import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from database_environment import TestDatabase
from pawspot import db
from pawspot.config import get_settings


@pytest.fixture(scope="session", autouse=True)
def isolated_integration_database(request: pytest.FixtureRequest) -> Iterator[None]:
    if not any(
        item.get_closest_marker("integration") for item in request.session.items
    ):
        yield
        return
    # Проверка выполняется до первого подключения; обычный .env не читаем.
    try:
        configuration = TestDatabase.from_environment(os.environ)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None
    schema = f"pawspot_suite_{uuid4().hex}"
    administration = create_engine(configuration.url, hide_parameters=True)
    engine = create_engine(
        configuration.url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"options": f"-c search_path={schema},public"},
    )
    created = False
    try:
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        with pytest.MonkeyPatch.context() as patch:
            for key, value in configuration.application_environment().items():
                patch.setenv(key, value)
            get_settings.cache_clear()
            db.get_engine.cache_clear()
            patch.setattr(db, "create_engine", lambda *args, **kwargs: engine)
            with engine.begin() as connection:
                config = Config("backend/alembic.ini")
                config.attributes["connection"] = connection
                config.attributes["version_table_schema"] = schema
                command.upgrade(config, "head")
                command.downgrade(config, "base")
                command.upgrade(config, "head")
            yield
    finally:
        db.get_engine.cache_clear()
        get_settings.cache_clear()
        engine.dispose()
        if created:
            # Удаляем только схему, созданную этим run, в выделенной test DB.
            with administration.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()
