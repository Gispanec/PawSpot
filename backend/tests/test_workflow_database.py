import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pawspot.config import Settings, get_settings
from pawspot.db import get_engine
from pawspot.main import app


def sample_jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 48), "gold").save(output, format="JPEG")
    return output.getvalue()


def headers(telegram_id: int) -> dict[str, str]:
    return {
        "X-Pawspot-Service-Token": "workflow-test-secret",
        "X-Pawspot-Telegram-Id": str(telegram_id),
    }


def create_draft(
    client: TestClient, actor_headers: dict[str, str]
) -> dict[str, object]:
    response = client.post("/internal/v1/encounter-drafts", headers=actor_headers)
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


def upload(
    client: TestClient, actor_headers: dict[str, str], draft: dict[str, object]
) -> dict[str, object]:
    response = client.put(
        f"/internal/v1/encounter-drafts/{draft['public_id']}/photo",
        headers=actor_headers,
        data={"expected_version": str(draft["version"])},
        files={"file": ("photo.jpg", sample_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


def patch(
    client: TestClient,
    actor_headers: dict[str, str],
    draft: dict[str, object],
    **changes: object,
) -> dict[str, object]:
    response = client.patch(
        f"/internal/v1/encounter-drafts/{draft['public_id']}",
        headers=actor_headers,
        json={"expected_version": draft["version"], **changes},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


@pytest.mark.integration
def test_new_existing_no_geo_matching_and_privacy(tmp_path: Path) -> None:
    telegram_id = uuid4().int & ((1 << 52) - 1)
    settings = Settings(
        db_password=SecretStr(""),
        internal_service_token=SecretStr("workflow-test-secret"),
        allowed_telegram_ids=str(telegram_id),
        media_dir=str(tmp_path),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    actor_headers = headers(telegram_id)
    try:
        with TestClient(app) as client:
            draft = create_draft(client, actor_headers)
            assert draft["state"] == "need_photo"
            bad_photo = client.put(
                f"/internal/v1/encounter-drafts/{draft['public_id']}/photo",
                headers=actor_headers,
                data={"expected_version": str(draft["version"])},
                files={"file": ("bad.jpg", b"invalid", "image/jpeg")},
            )
            assert bad_photo.status_code == 415
            draft = upload(client, actor_headers, draft)
            assert draft["photo_public_id"] is not None
            assert draft["city_name"] == "Tbilisi"
            assert "private_location" not in draft
            draft = patch(client, actor_headers, draft, species="dog")
            draft = patch(
                client,
                actor_headers,
                draft,
                location={"latitude": 41.7151, "longitude": 44.8271},
            )
            matches = client.post(
                f"/internal/v1/encounter-drafts/{draft['public_id']}/matches",
                headers=actor_headers,
            )
            assert matches.status_code == 200
            assert matches.json() == []
            draft = patch(client, actor_headers, draft, new_animal={"name": "Givi"})
            assert draft["state"] == "ready"
            assert draft["new_name"] == "Givi"
            assert draft["selected_animal_name"] is None
            draft = patch(client, actor_headers, draft, comment="У пекарни")
            resumed = client.get(
                "/internal/v1/encounter-drafts/current", headers=actor_headers
            )
            assert resumed.status_code == 200
            assert resumed.json()["new_name"] == "Givi"
            assert resumed.json()["comment"] == "У пекарни"
            assert resumed.json()["photo_public_id"] == draft["photo_public_id"]
            assert "private_location" not in resumed.text
            draft = patch(client, actor_headers, draft, comment=None)
            assert draft["new_name"] == "Givi"
            assert draft["comment"] is None
            result = client.post(
                f"/internal/v1/encounter-drafts/{draft['public_id']}/commit",
                headers=actor_headers,
                json={"expected_version": draft["version"]},
            )
            assert result.status_code == 201, result.text
            first = result.json()
            assert first["animal_name"] == "Givi"
            assert first["approximate_latitude"] != 41.7151
            assert "private_location" not in first
            assert "telegram_id" not in first
            repeated = client.post(
                f"/internal/v1/encounter-drafts/{draft['public_id']}/commit",
                headers=actor_headers,
                json={"expected_version": draft["version"]},
            )
            assert repeated.status_code == 200
            assert (
                repeated.json()["encounter_public_id"] == first["encounter_public_id"]
            )

            with get_engine().connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT ST_Y(private_location::geometry), "
                        "ST_Y(public_location::geometry) FROM encounters WHERE id = :id"
                    ),
                    {"id": first["encounter_public_id"]},
                ).one()
                assert row[0] == pytest.approx(41.7151)
                assert row[1] == pytest.approx(first["approximate_latitude"])
                assert connection.scalar(text("SELECT count(*) FROM animals")) == 1
                assert connection.scalar(text("SELECT count(*) FROM encounters")) == 1
            thumbnail = client.get(
                f"/internal/v1/photos/{first['photo_public_id']}/thumbnail",
                headers=actor_headers,
            )
            assert thumbnail.status_code == 200
            with Image.open(io.BytesIO(thumbnail.content)) as image:
                assert image.getexif() == {}

            second = create_draft(client, actor_headers)
            second = upload(client, actor_headers, second)
            second = patch(client, actor_headers, second, species="dog")
            second = patch(client, actor_headers, second, location=None)
            second = patch(
                client,
                actor_headers,
                second,
                animal_public_id=first["animal_public_id"],
            )
            assert second["selected_animal_name"] == "Givi"
            assert second["photo_public_id"] is not None
            result = client.post(
                f"/internal/v1/encounter-drafts/{second['public_id']}/commit",
                headers=actor_headers,
                json={"expected_version": second["version"]},
            )
            assert result.status_code == 201, result.text
            assert result.json()["animal_public_id"] == first["animal_public_id"]
            assert result.json()["approximate_latitude"] is None
            assert result.json()["comment"] is None

            third = create_draft(client, actor_headers)
            third = upload(client, actor_headers, third)
            third = patch(client, actor_headers, third, species="dog")
            third = patch(
                client,
                actor_headers,
                third,
                location={"latitude": 41.7152, "longitude": 44.8272},
            )
            matches = client.post(
                f"/internal/v1/encounter-drafts/{third['public_id']}/matches",
                headers=actor_headers,
            )
            assert matches.status_code == 200
            assert [item["animal_public_id"] for item in matches.json()] == [
                first["animal_public_id"]
            ]
            assert matches.json()[0]["thumbnail_photo_id"] == first["photo_public_id"]
            assert matches.json()[0]["encounter_count"] == 2
            assert matches.json()[0]["last_observed_at"]
            assert "private_location" not in matches.text
            assert "approximate_latitude" not in matches.text
            assert all("distance" not in item for item in matches.json())
            candidate_photo = client.get(
                f"/internal/v1/photos/{matches.json()[0]['thumbnail_photo_id']}/main",
                headers=actor_headers,
            )
            assert candidate_photo.status_code == 200
            third = patch(
                client,
                actor_headers,
                third,
                animal_public_id=first["animal_public_id"],
            )

            def commit_third() -> tuple[int, str]:
                with TestClient(app) as concurrent_client:
                    response = concurrent_client.post(
                        f"/internal/v1/encounter-drafts/{third['public_id']}/commit",
                        headers=actor_headers,
                        json={"expected_version": third["version"]},
                    )
                    return response.status_code, response.json()["encounter_public_id"]

            with ThreadPoolExecutor(max_workers=2) as pool:
                concurrent_results = list(pool.map(lambda _: commit_third(), range(2)))
            assert {status for status, _ in concurrent_results} == {200, 201}
            assert len({encounter_id for _, encounter_id in concurrent_results}) == 1
            collection = client.get(
                "/internal/v1/users/me/collection", headers=actor_headers
            )
            assert collection.status_code == 200
            assert len(collection.json()) == 1
            assert (
                collection.json()[0]["thumbnail_photo_id"] == first["photo_public_id"]
            )
            with get_engine().connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM animals")) == 1
                assert connection.scalar(text("SELECT count(*) FROM encounters")) == 3

            with get_engine().begin() as connection:
                connection.execute(
                    text(
                        "UPDATE encounters SET observed_at = "
                        "now() - interval '120 days'"
                    )
                )
            old_matches_draft = create_draft(client, actor_headers)
            old_matches_draft = upload(client, actor_headers, old_matches_draft)
            old_matches_draft = patch(
                client, actor_headers, old_matches_draft, species="dog"
            )
            old_matches_draft = patch(
                client,
                actor_headers,
                old_matches_draft,
                location={"latitude": 41.7151, "longitude": 44.8271},
            )
            old_matches = client.post(
                f"/internal/v1/encounter-drafts/{old_matches_draft['public_id']}/matches",
                headers=actor_headers,
            )
            assert old_matches.status_code == 200
            assert old_matches.json() == []
            assert (
                client.delete(
                    f"/internal/v1/encounter-drafts/{old_matches_draft['public_id']}",
                    headers=actor_headers,
                ).status_code
                == 204
            )

            rollback_draft = create_draft(client, actor_headers)
            rollback_draft = upload(client, actor_headers, rollback_draft)
            rollback_draft = patch(client, actor_headers, rollback_draft, species="dog")
            rollback_draft = patch(client, actor_headers, rollback_draft, location=None)
            rollback_draft = patch(
                client, actor_headers, rollback_draft, new_animal={"name": "Rollback"}
            )
            with get_engine().begin() as connection:
                connection.execute(
                    text(
                        "UPDATE encounter_drafts SET photo_id = "
                        "CAST(:photo_id AS uuid) "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {
                        "photo_id": first["photo_public_id"],
                        "id": rollback_draft["public_id"],
                    },
                )
            with pytest.raises(IntegrityError):
                client.post(
                    f"/internal/v1/encounter-drafts/{rollback_draft['public_id']}/commit",
                    headers=actor_headers,
                    json={"expected_version": rollback_draft["version"]},
                )
            with get_engine().connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM animals")) == 1
                assert connection.scalar(text("SELECT count(*) FROM encounters")) == 3
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
def test_new_animal_without_location_name_or_comment(tmp_path: Path) -> None:
    telegram_id = uuid4().int & ((1 << 52) - 1)
    settings = Settings(
        db_password=SecretStr(""),
        internal_service_token=SecretStr("workflow-test-secret"),
        allowed_telegram_ids=str(telegram_id),
        media_dir=str(tmp_path),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            draft = create_draft(client, headers(telegram_id))
            draft = upload(client, headers(telegram_id), draft)
            draft = patch(client, headers(telegram_id), draft, species="cat")
            draft = patch(client, headers(telegram_id), draft, location=None)
            draft = patch(client, headers(telegram_id), draft, new_animal={})
            response = client.post(
                f"/internal/v1/encounter-drafts/{draft['public_id']}/commit",
                headers=headers(telegram_id),
                json={"expected_version": draft["version"]},
            )
            assert response.status_code == 201, response.text
            assert response.json()["animal_name"] is None
            assert response.json()["comment"] is None
            assert response.json()["approximate_latitude"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
def test_observed_time_and_today_encounter_feed(tmp_path: Path) -> None:
    telegram_id = uuid4().int & ((1 << 52) - 1)
    settings = Settings(
        db_password=SecretStr(""),
        internal_service_token=SecretStr("workflow-test-secret"),
        allowed_telegram_ids=str(telegram_id),
        media_dir=str(tmp_path),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    actor = headers(telegram_id)
    try:
        with TestClient(app) as client:
            url = "/internal/v1/feed/today"
            assert client.get(url).status_code == 401
            assert (
                client.get(url, headers=actor, params={"page_size": 6}).status_code
                == 422
            )
            before = client.get(url, headers=actor).json()
            first_animal: str | None = None
            created: list[str] = []
            for index in range(7):
                draft = create_draft(client, actor)
                original_time = datetime.fromisoformat(str(draft["observed_at"]))
                assert abs((datetime.now(UTC) - original_time).total_seconds()) < 60
                draft = upload(client, actor, draft)
                species = "cat" if index == 5 else "dog"
                draft = patch(client, actor, draft, species=species)
                draft = patch(client, actor, draft, location=None)
                if index in {0, 5}:
                    draft = patch(
                        client, actor, draft, new_animal={"name": f"Today {index}"}
                    )
                else:
                    assert first_animal is not None
                    draft = patch(client, actor, draft, animal_public_id=first_animal)
                if index == 6:
                    draft = patch(client, actor, draft, comment="Вчера у кафе")
                    yesterday = datetime.now(ZoneInfo("Asia/Tbilisi")) - timedelta(
                        days=1
                    )
                    draft = patch(
                        client, actor, draft, observed_at=yesterday.isoformat()
                    )
                    assert draft["comment"] == "Вчера у кафе"
                    assert draft["selection"] == "existing"
                    draft = patch(
                        client,
                        actor,
                        draft,
                        location={"latitude": 41.7151, "longitude": 44.8271},
                    )
                    assert draft["comment"] == "Вчера у кафе"
                    assert (
                        datetime.fromisoformat(str(draft["observed_at"])) == yesterday
                    )
                    assert draft["location_present"] is True
                    future = client.patch(
                        f"/internal/v1/encounter-drafts/{draft['public_id']}",
                        headers=actor,
                        json={
                            "expected_version": draft["version"],
                            "observed_at": (
                                datetime.now(UTC) + timedelta(days=1)
                            ).isoformat(),
                        },
                    )
                    assert future.status_code == 422
                response = client.post(
                    f"/internal/v1/encounter-drafts/{draft['public_id']}/commit",
                    headers=actor,
                    json={"expected_version": draft["version"]},
                )
                assert response.status_code == 201, response.text
                result = response.json()
                assert "private_location" not in response.text
                if index == 0:
                    first_animal = result["animal_public_id"]
                if index == 6:
                    with get_engine().connect() as connection:
                        stored = connection.execute(
                            text(
                                "SELECT created_at, observed_at, "
                                "ST_Y(private_location::geometry), "
                                "ST_Y(public_location::geometry) "
                                "FROM encounters WHERE id = CAST(:id AS uuid)"
                            ),
                            {"id": result["encounter_public_id"]},
                        ).one()
                    assert stored[0] - stored[1] > timedelta(hours=23)
                    assert stored[2] == pytest.approx(41.7151)
                    assert stored[3] != pytest.approx(41.7151)
                else:
                    created.append(result["encounter_public_id"])
            after = client.get(url, headers=actor).json()
            assert after["encounters"] == before["encounters"] + 6
            assert after["dogs"] == before["dogs"] + 5
            assert after["cats"] == before["cats"] + 1
            assert after["first_animals"] == before["first_animals"] + 2
            found: list[str] = []
            for page_number in range(1, 3):
                page = client.get(
                    url, headers=actor, params={"page": page_number, "page_size": 5}
                )
                assert page.status_code == 200, page.text
                assert "private_location" not in page.text
                assert "telegram_id" not in page.text
                assert len(page.json()["items"]) <= 5
                found.extend(
                    item["encounter_public_id"] for item in page.json()["items"]
                )
            assert set(created).issubset(set(found))
            assert any(
                item["repeat_encounter"]
                for item in client.get(
                    url, headers=actor, params={"page_size": 5}
                ).json()["items"]
                if item["animal_public_id"] == first_animal
            )
    finally:
        app.dependency_overrides.clear()
