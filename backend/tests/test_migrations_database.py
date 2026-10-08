import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg.errors import RaiseException
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from database_environment import TestDatabase
from pawspot.db import get_engine
from pawspot.main import app

pytestmark = pytest.mark.integration
PREVIOUS = "20261004_02"
HEAD = "20261004_03"
USER = UUID(int=1)
ANIMAL = UUID(int=2)
PHOTO = UUID(int=3)
ENCOUNTER = UUID(int=4)
TABLES = (
    "users",
    "cities",
    "photos",
    "animals",
    "encounters",
    "reactions",
    "auth_sessions",
)


@pytest.fixture
def migration_database() -> Iterator[tuple[Engine, Config]]:
    # Повторяем guard до подключения; схему suite и pilot не используем.
    configuration = TestDatabase.from_environment(os.environ)
    schema = f"pawspot_migration_{uuid4().hex}"
    administration = create_engine(configuration.url, hide_parameters=True)
    engine = create_engine(
        configuration.url,
        hide_parameters=True,
        connect_args={"options": f"-c search_path={schema},public"},
    )
    created = False
    try:
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        config = Config("backend/alembic.ini")
        config.attributes["version_table_schema"] = schema
        yield engine, config
    finally:
        engine.dispose()
        if created:
            with administration.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()


def upgrade(engine: Engine, config: Config, revision: str) -> None:
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def seed_previous(
    connection: Connection, *, without_photo: bool = False, deleted: bool = False
) -> None:
    # Только SQL старой схемы: текущие ORM defaults/constraints здесь не применяем.
    connection.execute(
        text(
            "INSERT INTO users (id, telegram_id, display_name) "
            "VALUES (:id, 123456789, 'Migration author'), "
            "(:reader, 987654321, 'Migration reader')"
        ),
        {"id": USER, "reader": UUID(int=9)},
    )
    connection.execute(
        text(
            "INSERT INTO photos (id, uploaded_by_user_id, storage_key, thumbnail_key, "
            "actual_mime, bytes, width, height) "
            "VALUES (:id, :user, 'fixture/main.jpg', 'fixture/thumb.jpg', "
            "'image/jpeg', 100, 10, 10)"
        ),
        {"id": PHOTO, "user": USER},
    )
    connection.execute(
        text(
            "INSERT INTO animals (id, species, name, primary_photo_id, city_id, "
            "created_by_user_id) SELECT :id, 'dog', 'Givi', :photo, id, :user "
            "FROM cities WHERE slug = 'tbilisi'"
        ),
        {"id": ANIMAL, "photo": PHOTO, "user": USER},
    )
    connection.execute(
        text(
            "INSERT INTO encounters (id, animal_id, user_id, photo_id, city_id, "
            "private_location, public_location, public_cell_key, geo_policy_version, "
            "comment, observed_at, deleted_at) "
            "SELECT :id, :animal, :user, :photo, id, "
            "ST_GeogFromText('SRID=4326;POINT(44.8271 41.7151)'), "
            "ST_GeogFromText('SRID=4326;POINT(44.828 41.716)'), 'fixture-cell', 1, "
            "'Old encounter', :observed, :deleted FROM cities WHERE slug = 'tbilisi'"
        ),
        {
            "id": ENCOUNTER,
            "animal": ANIMAL,
            "user": USER,
            "photo": None if without_photo else PHOTO,
            "observed": datetime(2026, 10, 1, 12, tzinfo=UTC),
            "deleted": datetime(2026, 10, 2, tzinfo=UTC) if deleted else None,
        },
    )
    connection.execute(
        text(
            "INSERT INTO reactions (id, user_id, encounter_id, reaction_type) "
            "VALUES (:id, :user, :encounter, 'heart')"
        ),
        {"id": UUID(int=5), "user": UUID(int=9), "encounter": ENCOUNTER},
    )
    connection.execute(
        text(
            "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at) "
            "VALUES (:id, :user, :hash, :expires)"
        ),
        {
            "id": UUID(int=6),
            "user": USER,
            "hash": "0" * 64,
            "expires": datetime(2026, 10, 2, tzinfo=UTC),
        },
    )


def snapshot(connection: Connection) -> dict[str, list[dict[str, object]]]:
    return {
        table: [
            dict(row)
            for row in connection.execute(
                text(f'SELECT * FROM "{table}" ORDER BY id')
            ).mappings()
        ]
        for table in TABLES
    }


def test_upgrade_preserves_previous_data_and_enforces_new_schema(
    migration_database: tuple[Engine, Config],
) -> None:
    engine, config = migration_database
    upgrade(engine, config, PREVIOUS)
    with engine.begin() as connection:
        seed_previous(connection)
        before = snapshot(connection)
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == PREVIOUS
        )
    upgrade(engine, config, "head")
    with engine.connect() as connection:
        assert snapshot(connection) == before
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        )
        assert connection.scalar(text("SELECT count(*) FROM encounter_drafts")) == 0
        assert photo_nullable(connection) == "NO"
        # Старая схема разрешала NULL. Новая должна отклонять его на уровне БД.
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("UPDATE encounters SET photo_id = NULL WHERE id = :id"),
                {"id": ENCOUNTER},
            )
        assert snapshot(connection) == before
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO encounter_drafts "
                    "(id, user_id, observed_at, expires_at) "
                    "VALUES (:id, :user, :observed, :expires)"
                ),
                {
                    "id": UUID(int=7),
                    "user": USER,
                    "observed": datetime(2026, 10, 1, tzinfo=UTC),
                    "expires": datetime(2026, 10, 2, tzinfo=UTC),
                },
            )
        draft = connection.execute(
            text("SELECT status, version, location_set FROM encounter_drafts")
        ).one()
        assert tuple(draft) == ("active", 0, False)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("UPDATE encounter_drafts SET version = -1"))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO encounter_drafts "
                    "(id, user_id, observed_at, expires_at) "
                    "SELECT :id, user_id, observed_at, expires_at FROM encounter_drafts"
                ),
                {"id": UUID(int=8)},
            )


@pytest.mark.parametrize("deleted", [False, True])
def test_upgrade_refuses_legacy_photoless_encounters_without_data_loss(
    migration_database: tuple[Engine, Config],
    deleted: bool,
) -> None:
    engine, config = migration_database
    upgrade(engine, config, PREVIOUS)
    with engine.begin() as connection:
        seed_previous(connection, without_photo=True, deleted=deleted)
        before = snapshot(connection)
    with pytest.raises(
        DBAPIError, match="20261004_03.*encounters.photo_id.*NULL"
    ) as error:
        upgrade(engine, config, "head")
    assert isinstance(error.value.orig, RaiseException)
    assert error.value.orig.sqlstate == "P0001"
    with engine.connect() as connection:
        assert snapshot(connection) == before
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == PREVIOUS
        )
        assert not inspect(connection).has_table("encounter_drafts")
        assert photo_nullable(connection) == "YES"


def photo_nullable(connection: Connection) -> str:
    return str(
        connection.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'encounters' "
                "AND column_name = 'photo_id'"
            )
        )
    )


@pytest.mark.parametrize(
    "revision,expected", [(HEAD, 200), (PREVIOUS, 503), ("base", 503), (None, 503)]
)
def test_readiness_checks_real_schema(
    migration_database: tuple[Engine, Config],
    revision: str | None,
    expected: int,
) -> None:
    engine, config = migration_database
    if revision is not None:
        upgrade(engine, config, revision)
    with engine.connect() as connection:
        tables_before = inspect(connection).get_table_names()
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        with TestClient(app) as client:
            response = client.get("/ready")
    finally:
        app.dependency_overrides.pop(get_engine, None)
    assert response.status_code == expected
    if expected == 503:
        assert response.json() == {"detail": "Database is not ready"}
    with engine.connect() as connection:
        assert inspect(connection).get_table_names() == tables_before
        if revision not in (None, "base"):
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == revision
            )


def test_readiness_does_not_use_version_from_another_schema(
    migration_database: tuple[Engine, Config],
) -> None:
    _, config = migration_database
    configuration = TestDatabase.from_environment(os.environ)
    with get_engine().connect() as connection:
        suite_schema = connection.scalar(text("SELECT current_schema()"))
    empty_schema = config.attributes["version_table_schema"]
    engine = create_engine(
        configuration.url,
        hide_parameters=True,
        connect_args={
            "options": f"-c search_path={empty_schema},{suite_schema},public"
        },
    )
    try:
        with engine.connect() as connection:
            # Неквалифицированный SELECT действительно видит head чужой схемы.
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == HEAD
            )
        app.dependency_overrides[get_engine] = lambda: engine
        with TestClient(app) as client:
            response = client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database is not ready"}
    finally:
        app.dependency_overrides.pop(get_engine, None)
        engine.dispose()
