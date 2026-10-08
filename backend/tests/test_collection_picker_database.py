from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pawspot.config import Settings, get_settings
from pawspot.db import get_engine, get_session
from pawspot.main import app
from pawspot.models import Animal, City, Encounter, Photo, User
from test_workflow_database import create_draft, headers, patch, upload


@pytest.mark.integration
@pytest.mark.parametrize("size", [150, 505])
def test_collection_picker_pages_search_isolation_and_selection(
    tmp_path: Path, size: int
) -> None:
    owner_id = uuid4().int & ((1 << 52) - 1)
    stranger_id = owner_id + 1
    settings = Settings(
        db_password=SecretStr(""),
        internal_service_token=SecretStr("workflow-test-secret"),
        allowed_telegram_ids=f"{owner_id},{stranger_id}",
        media_dir=str(tmp_path),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    # Внешняя транзакция изолирует seed от остальных тестов общей suite-схемы.
    with get_engine().connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:

            def database() -> Iterator[Session]:
                yield session

            app.dependency_overrides[get_session] = database
            try:
                owner = User(telegram_id=owner_id, display_name="Owner")
                stranger = User(telegram_id=stranger_id, display_name="Stranger")
                session.add_all([owner, stranger])
                session.flush()
                city = session.scalar(select(City).where(City.slug == "tbilisi"))
                assert city is not None
                dogs: list[Animal] = []
                moment = datetime(2026, 10, 1, tzinfo=UTC)

                def encounter(
                    animal: Animal,
                    actor: User,
                    observed: datetime,
                    *,
                    deleted: bool = False,
                ) -> None:
                    photo_id = uuid4()
                    photo = Photo(
                        id=photo_id,
                        uploaded_by_user_id=actor.id,
                        storage_key=f"{photo_id}/main.jpg",
                        thumbnail_key=f"{photo_id}/thumbnail.jpg",
                        actual_mime="image/jpeg",
                        bytes=10,
                        width=10,
                        height=10,
                    )
                    session.add(photo)
                    session.flush()
                    if animal.primary_photo_id is None:
                        animal.primary_photo_id = photo.id
                    session.add(
                        Encounter(
                            animal_id=animal.id,
                            user_id=actor.id,
                            photo_id=photo.id,
                            city_id=city.id,
                            observed_at=observed,
                            deleted_at=moment if deleted else None,
                        )
                    )

                for index in range(size):
                    name = (
                        "Красношейка"
                        if index == size - 1
                        else "ბონდო"
                        if index == size - 2
                        else "100%_Givi/"
                        if index == size - 3
                        else None
                        if index == size - 4
                        else "Бондо"
                    )
                    animal = Animal(
                        species="dog",
                        name=name,
                        created_by_user_id=owner.id,
                        city_id=city.id,
                    )
                    session.add(animal)
                    session.flush()
                    # Равные даты у соседей проверяют стабильную сортировку по UUID.
                    encounter(animal, owner, moment - timedelta(minutes=index // 2))
                    dogs.append(animal)
                encounter(dogs[0], owner, moment - timedelta(days=1))
                encounter(dogs[0], stranger, moment + timedelta(days=1))
                excluded: list[Animal] = []
                for species, status, actor, deleted in [
                    ("cat", "active", owner, False),
                    ("cat", "active", owner, False),
                    ("dog", "hidden", owner, False),
                    ("dog", "merged", owner, False),
                    ("dog", "active", owner, True),
                    ("dog", "active", stranger, False),
                ]:
                    animal = Animal(
                        species=species,
                        status=status,
                        created_by_user_id=actor.id,
                        city_id=city.id,
                        name="Красношейка",
                        merged_into_id=dogs[0].id if status == "merged" else None,
                    )
                    session.add(animal)
                    session.flush()
                    encounter(animal, actor, moment, deleted=deleted)
                    excluded.append(animal)
                session.commit()
                url = "/internal/v1/users/me/collection-picker"
                actor_headers = headers(owner_id)
                with TestClient(app) as client:
                    found: list[str] = []
                    all_items: list[dict[str, Any]] = []
                    for page in range(1, size // 5 + 1):
                        response = client.get(
                            url,
                            headers=actor_headers,
                            params={"species": "dog", "page": page},
                        )
                        assert response.status_code == 200, response.text
                        result = response.json()
                        assert result["page"] == page
                        assert len(result["items"]) == 5
                        assert result["has_next"] == (page < size // 5)
                        assert "location" not in response.text
                        found.extend(i["animal_public_id"] for i in result["items"])
                        all_items.extend(result["items"])
                        again = client.get(
                            url,
                            headers=actor_headers,
                            params={"species": "dog", "page": page},
                        )
                        assert again.json() == result
                    assert len(found) == len(set(found)) == size
                    assert set(found) == {str(d.id) for d in dogs}
                    assert any(i["name"] is None for i in all_items)
                    assert (
                        next(
                            i
                            for i in all_items
                            if i["animal_public_id"] == str(dogs[0].id)
                        )["encounter_count"]
                        == 2
                    )
                    # Чужая встреча не меняет личные дату/счётчик/порядок.
                    assert (
                        datetime.fromisoformat(all_items[0]["last_observed_at"])
                        == moment
                    )
                    assert not client.get(
                        url,
                        headers=actor_headers,
                        params={"species": "dog", "page": size // 5 + 1},
                    ).json()["has_next"]
                    assert (
                        client.get(
                            url,
                            headers=actor_headers,
                            params={"species": "dog", "page": size // 5 + 1},
                        ).json()["items"]
                        == []
                    )
                    for query, index in [
                        ("  красН  ", -1),
                        ("ბონ", -2),
                        ("%", -3),
                        ("_", -3),
                        ("gIvI/", -3),
                    ]:
                        response = client.get(
                            url,
                            headers=actor_headers,
                            params={"species": "dog", "q": query},
                        )
                        assert response.status_code == 200
                        assert [
                            i["animal_public_id"] for i in response.json()["items"]
                        ] == [str(dogs[index].id)]
                    for page in (1, 2, size // 5):
                        response = client.get(
                            url,
                            headers=actor_headers,
                            params={"species": "dog", "q": "бонДО", "page": page},
                        )
                        assert all(
                            i["name"] == "Бондо" for i in response.json()["items"]
                        )
                    cats = client.get(
                        url, headers=actor_headers, params={"species": "cat"}
                    ).json()["items"]
                    assert len(cats) == 2
                    assert all(i["species"] == "cat" for i in cats)
                    other = client.get(
                        url, headers=headers(stranger_id), params={"species": "dog"}
                    ).json()["items"]
                    assert {i["animal_public_id"] for i in other} == {
                        str(dogs[0].id),
                        str(excluded[-1].id),
                    }
                    assert client.get(url, params={"species": "dog"}).status_code == 401
                    assert (
                        client.get(
                            url,
                            headers=headers(stranger_id + 1),
                            params={"species": "dog"},
                        ).status_code
                        == 403
                    )
                    for params in [
                        {},
                        {"species": "bird"},
                        {"species": "dog", "page": 0},
                        {"species": "dog", "page": 100001},
                        {"species": "dog", "page_size": 0},
                        {"species": "dog", "page_size": 6},
                        {"species": "dog", "q": "x" * 81},
                    ]:
                        assert (
                            client.get(
                                url, headers=actor_headers, params=params
                            ).status_code
                            == 422
                        )
                    last_id = found[-1]
                    detail = client.get(
                        f"{url}/{last_id}",
                        headers=actor_headers,
                        params={"species": "dog"},
                    )
                    assert detail.status_code == 200
                    for invalid in excluded:
                        assert (
                            client.get(
                                f"{url}/{invalid.id}",
                                headers=actor_headers,
                                params={"species": "dog"},
                            ).status_code
                            == 404
                        )
                    assert (
                        client.get(
                            f"{url}/{last_id}",
                            headers=headers(stranger_id),
                            params={"species": "dog"},
                        ).status_code
                        == 404
                    )
                    draft = upload(
                        client, actor_headers, create_draft(client, actor_headers)
                    )
                    draft = patch(
                        client, actor_headers, draft, species="dog", location=None
                    )
                    for invalid in excluded:
                        response = client.patch(
                            f"/internal/v1/encounter-drafts/{draft['public_id']}",
                            headers=actor_headers,
                            json={
                                "expected_version": draft["version"],
                                "animal_public_id": str(invalid.id),
                            },
                        )
                        assert response.status_code == 422
                        session.rollback()
                    animal_count = session.scalar(
                        select(func.count()).select_from(Animal)
                    )
                    draft = patch(
                        client, actor_headers, draft, animal_public_id=last_id
                    )
                    draft = patch(client, actor_headers, draft, location=None)
                    assert draft["animal_public_id"] == last_id
                    commit_url = (
                        f"/internal/v1/encounter-drafts/{draft['public_id']}/commit"
                    )
                    response = client.post(
                        commit_url,
                        headers=actor_headers,
                        json={"expected_version": draft["version"]},
                    )
                    assert response.status_code == 201, response.text
                    assert response.json()["animal_public_id"] == last_id
                    assert response.json()["approximate_latitude"] is None
                    repeated = client.post(
                        commit_url,
                        headers=actor_headers,
                        json={"expected_version": draft["version"]},
                    )
                    assert repeated.status_code == 200
                    assert (
                        repeated.json()["encounter_public_id"]
                        == response.json()["encounter_public_id"]
                    )
                    assert (
                        session.scalar(select(func.count()).select_from(Animal))
                        == animal_count
                    )
                    # Недоступность после открытия проверяется снова при PATCH.
                    draft = upload(
                        client, actor_headers, create_draft(client, actor_headers)
                    )
                    draft = patch(
                        client, actor_headers, draft, species="dog", location=None
                    )
                    dogs[-1].status = "hidden"
                    session.commit()
                    assert (
                        client.get(
                            f"{url}/{dogs[-1].id}",
                            headers=actor_headers,
                            params={"species": "dog"},
                        ).status_code
                        == 404
                    )
                    response = client.patch(
                        f"/internal/v1/encounter-drafts/{draft['public_id']}",
                        headers=actor_headers,
                        json={
                            "expected_version": draft["version"],
                            "animal_public_id": str(dogs[-1].id),
                        },
                    )
                    assert response.status_code == 422
            finally:
                app.dependency_overrides.clear()
                session.rollback()
                transaction.rollback()
