from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pawspot.db import get_engine
from pawspot.geo import point_wkt, public_location
from pawspot.models import Animal, City, Encounter, Photo, Reaction, User


@pytest.mark.integration
@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(41.7151, 44.8271), (51.5074, -0.1278), (0, 0), (0, 180), (89.999, 10)],
)
def test_public_point_postgis_distance(latitude: float, longitude: float) -> None:
    point = public_location(latitude, longitude, 200)
    with get_engine().connect() as connection:
        distance = connection.scalar(
            text(
                "SELECT ST_Distance(ST_GeogFromText(:private), "
                "ST_GeogFromText(:public))"
            ),
            {
                "private": point_wkt(latitude, longitude),
                "public": point_wkt(point.latitude, point.longitude),
            },
        )
    assert distance is not None
    assert 0 <= distance < 300


@pytest.mark.integration
def test_migration_schema_and_seed() -> None:
    engine = get_engine()
    tables = set(inspect(engine).get_table_names())
    assert {
        "alembic_version",
        "auth_sessions",
        "encounter_drafts",
        "users",
        "cities",
        "photos",
        "animals",
        "encounters",
        "reactions",
    } <= tables
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT extname FROM pg_extension WHERE extname = 'postgis'")
            )
            == "postgis"
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "20261004_03"
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM cities WHERE slug = 'tbilisi'")
            )
            == 1
        )
        index = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'encounters' "
                "AND indexname = 'ix_encounters_public_location_gist'"
            )
        ).scalar_one()
        assert "USING gist" in index
        assert "public_location" in index


@pytest.mark.integration
def test_relationships_geo_and_constraints() -> None:
    engine = get_engine()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(
                bind=connection, join_transaction_mode="create_savepoint"
            ) as session:
                city = session.scalar(select(City).where(City.slug == "tbilisi"))
                assert city is not None
                author = User(
                    telegram_id=uuid4().int & ((1 << 63) - 1), display_name="Author"
                )
                reader = User(
                    telegram_id=uuid4().int & ((1 << 63) - 1), display_name="Reader"
                )
                session.add_all([author, reader])
                session.flush()
                animal = Animal(
                    species="dog",
                    name="Givi",
                    city_id=city.id,
                    created_by_user_id=author.id,
                )
                session.add(animal)
                session.flush()
                photo = Photo(
                    uploaded_by_user_id=author.id,
                    storage_key=f"{uuid4()}/main.jpg",
                    thumbnail_key=f"{uuid4()}/thumbnail.jpg",
                    actual_mime="image/jpeg",
                    bytes=100,
                    width=10,
                    height=10,
                )
                session.add(photo)
                session.flush()
                point = public_location(41.7151, 44.8271, 200)
                encounter = Encounter(
                    animal=animal,
                    user=author,
                    city=city,
                    photo_id=photo.id,
                    observed_at=datetime.now(UTC),
                    private_location=func.ST_GeogFromText(point_wkt(41.7151, 44.8271)),
                    public_location=func.ST_GeogFromText(
                        point_wkt(point.latitude, point.longitude)
                    ),
                    public_cell_key=point.cell_key,
                    geo_policy_version=point.policy_version,
                )
                session.add(encounter)
                session.flush()
                assert encounter in author.encounters
                assert encounter in animal.encounters
                assert encounter in city.encounters
                stored = session.execute(
                    text(
                        "SELECT ST_Y(private_location::geometry), "
                        "ST_Y(public_location::geometry), "
                        "ST_Distance(private_location, public_location) "
                        "FROM encounters WHERE id = :id"
                    ),
                    {"id": encounter.id},
                ).one()
                assert stored[0] == pytest.approx(41.7151)
                assert stored[1] == pytest.approx(point.latitude)
                assert 0 <= stored[2] < 300
                session.add(
                    Reaction(
                        user_id=reader.id,
                        encounter_id=encounter.id,
                        reaction_type="heart",
                    )
                )
                session.flush()

                def rejects(statement: str, parameters: dict[str, object]) -> None:
                    if ":photo_id" in statement:
                        invalid_photo_id = uuid4()
                        session.add(
                            Photo(
                                id=invalid_photo_id,
                                uploaded_by_user_id=author.id,
                                storage_key=f"{invalid_photo_id}/main.jpg",
                                thumbnail_key=f"{invalid_photo_id}/thumbnail.jpg",
                                actual_mime="image/jpeg",
                                bytes=100,
                                width=10,
                                height=10,
                            )
                        )
                        session.flush()
                        parameters = {**parameters, "photo_id": invalid_photo_id}
                    with pytest.raises(IntegrityError), session.begin_nested():
                        session.execute(text(statement), parameters)

                rejects(
                    "INSERT INTO reactions (id,user_id,encounter_id,reaction_type) "
                    "VALUES (:id,:user_id,:encounter_id,'heart')",
                    {"id": uuid4(), "user_id": reader.id, "encounter_id": encounter.id},
                )
                rejects(
                    "INSERT INTO reactions (id,user_id,encounter_id,reaction_type) "
                    "VALUES (:id,:user_id,:encounter_id,'wrong')",
                    {"id": uuid4(), "user_id": reader.id, "encounter_id": encounter.id},
                )
                rejects(
                    "INSERT INTO encounters "
                    "(id,animal_id,user_id,photo_id,city_id,observed_at) "
                    "VALUES (:id,:animal_id,:user_id,:photo_id,NULL,now())",
                    {
                        "id": uuid4(),
                        "animal_id": animal.id,
                        "user_id": author.id,
                        "photo_id": photo.id,
                    },
                )
                rejects(
                    "INSERT INTO encounters "
                    "(id,animal_id,user_id,photo_id,city_id,observed_at) "
                    "VALUES (:id,:animal_id,:user_id,:photo_id,:city_id,now())",
                    {
                        "id": uuid4(),
                        "animal_id": uuid4(),
                        "user_id": author.id,
                        "photo_id": photo.id,
                        "city_id": city.id,
                    },
                )
                rejects(
                    "INSERT INTO encounters "
                    "(id,animal_id,user_id,photo_id,city_id,"
                    "observed_at,public_location) "
                    "VALUES (:id,:animal_id,:user_id,:photo_id,:city_id,now(),"
                    "ST_GeogFromText('SRID=4326;POINT(44.82 41.71)'))",
                    {
                        "id": uuid4(),
                        "animal_id": animal.id,
                        "user_id": author.id,
                        "photo_id": photo.id,
                        "city_id": city.id,
                    },
                )
                rejects(
                    "INSERT INTO animals (id,species,created_by_user_id) "
                    "VALUES (:id,'rabbit',:user_id)",
                    {"id": uuid4(), "user_id": author.id},
                )
        finally:
            transaction.rollback()
