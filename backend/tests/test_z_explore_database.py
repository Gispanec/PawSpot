from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from pawspot.config import Settings, get_settings
from pawspot.db import get_engine
from pawspot.main import app
from test_auth import BOT_TOKEN, signed_init_data
from test_workflow_database import create_draft, headers, patch, upload


def make_encounter(
    client: TestClient,
    telegram_id: int,
    *,
    species: str,
    location: dict[str, float] | None,
    name: str | None = None,
    animal_id: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    actor_headers = headers(telegram_id)
    draft = create_draft(client, actor_headers)
    draft = upload(client, actor_headers, draft)
    draft = patch(client, actor_headers, draft, species=species)
    draft = patch(client, actor_headers, draft, location=location)
    if animal_id:
        draft = patch(client, actor_headers, draft, animal_public_id=animal_id)
    else:
        draft = patch(client, actor_headers, draft, new_animal={"name": name})
    if comment:
        draft = patch(client, actor_headers, draft, comment=comment)
    response = client.post(
        f"/internal/v1/encounter-drafts/{draft['public_id']}/commit",
        headers=actor_headers,
        json={"expected_version": draft["version"]},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def auth_headers(client: TestClient, telegram_id: int) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/telegram", json={"init_data": signed_init_data(telegram_id)}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.mark.integration
def test_explore_api_privacy_reactions_and_collection(tmp_path: Path) -> None:
    first_id = uuid4().int & ((1 << 52) - 1)
    second_id = uuid4().int & ((1 << 52) - 1)
    settings = Settings(
        db_password=SecretStr(""),
        telegram_bot_token=SecretStr(BOT_TOKEN),
        internal_service_token=SecretStr("workflow-test-secret"),
        allowed_telegram_ids=f"{first_id},{second_id}",
        media_dir=str(tmp_path),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            first = make_encounter(
                client,
                first_id,
                species="dog",
                location={"latitude": 41.7151, "longitude": 44.8271},
                name="Givi",
            )
            second = make_encounter(
                client,
                second_id,
                species="dog",
                location={"latitude": 41.7300, "longitude": 44.8500},
                animal_id=first["animal_public_id"],
                comment="У пекарни",
            )
            third = make_encounter(
                client,
                first_id,
                species="dog",
                location=None,
                animal_id=first["animal_public_id"],
            )
            cat = make_encounter(
                client, first_id, species="cat", location=None, name=None
            )
            with get_engine().begin() as connection:
                connection.execute(
                    text(
                        "UPDATE encounters SET created_at = now(), "
                        "observed_at = now() WHERE id IN "
                        "(CAST(:second AS uuid), CAST(:third AS uuid))"
                    ),
                    {
                        "second": second["encounter_public_id"],
                        "third": third["encounter_public_id"],
                    },
                )
            owner = auth_headers(client, first_id)
            observer = auth_headers(client, second_id)

            assert client.get("/api/v1/feed").status_code == 401
            seen: set[str] = set()
            cursor: str | None = None
            for _ in range(50):
                query = "?limit=1" + (f"&cursor={cursor}" if cursor else "")
                feed = client.get(f"/api/v1/feed{query}", headers=owner)
                assert feed.status_code == 200, feed.text
                assert "private_location" not in feed.text
                assert "storage_key" not in feed.text
                page = feed.json()
                for item in page["items"]:
                    assert item["public_id"] not in seen
                    seen.add(item["public_id"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            assert {
                first["encounter_public_id"],
                second["encounter_public_id"],
                third["encounter_public_id"],
                cat["encounter_public_id"],
            } <= seen
            assert (
                client.get("/api/v1/feed?cursor=bad", headers=owner).status_code == 422
            )

            animal = client.get(
                f"/api/v1/animals/{first['animal_public_id']}?limit=1", headers=owner
            )
            assert animal.status_code == 200, animal.text
            assert "private_location" not in animal.text
            body = animal.json()
            assert body["encounter_count"] == 3
            assert body["photo_count"] == 3
            assert body["observer_count"] == 2
            assert body["name"] == "Givi"
            unnamed = client.get(
                f"/api/v1/animals/{cat['animal_public_id']}", headers=owner
            )
            assert unnamed.status_code == 200
            assert unnamed.json()["name"] is None
            unnamed_encounter = unnamed.json()["timeline"]["items"][0]
            assert unnamed_encounter["approximate_latitude"] is None
            timeline_ids = {body["timeline"]["items"][0]["public_id"]}
            timeline_cursor = body["timeline"]["next_cursor"]
            while timeline_cursor:
                next_page = client.get(
                    f"/api/v1/animals/{first['animal_public_id']}?limit=1&cursor={timeline_cursor}",
                    headers=owner,
                ).json()["timeline"]
                for item in next_page["items"]:
                    assert item["public_id"] not in timeline_ids
                    timeline_ids.add(item["public_id"])
                timeline_cursor = next_page["next_cursor"]
            assert timeline_ids == {
                first["encounter_public_id"],
                second["encounter_public_id"],
                third["encounter_public_id"],
            }

            encounter_url = f"/api/v1/encounters/{second['encounter_public_id']}/detail"
            detail = client.get(encounter_url, headers=owner)
            assert detail.status_code == 200
            assert detail.json()["comment"] == "У пекарни"
            assert "private_location" not in detail.text
            assert "telegram_id" not in detail.text

            def map_at(point: dict[str, Any]) -> list[dict[str, Any]]:
                lat = point["approximate_latitude"]
                lon = point["approximate_longitude"]
                response = client.get(
                    f"/api/v1/map/animals?south={lat - 0.003}&north={lat + 0.003}"
                    f"&west={lon - 0.003}&east={lon + 0.003}",
                    headers=owner,
                )
                assert response.status_code == 200, response.text
                assert "private_location" not in response.text
                return cast(list[dict[str, Any]], response.json())

            assert first["animal_public_id"] not in {
                marker["animal_public_id"] for marker in map_at(first)
            }
            markers = map_at(second)
            current = next(
                marker
                for marker in markers
                if marker["animal_public_id"] == first["animal_public_id"]
            )
            assert current["encounter_public_id"] == second["encounter_public_id"]
            assert current["encounter_count"] == 3
            assert (
                client.get(
                    "/api/v1/map/animals?south=-90&west=-180&north=90&east=180",
                    headers=owner,
                ).status_code
                == 422
            )

            like_url = f"/api/v1/encounters/{second['encounter_public_id']}/like"
            assert client.put(like_url).status_code == 401
            assert client.put(like_url, headers=observer).status_code == 403
            for _ in range(2):
                liked = client.put(like_url, headers=owner)
                assert liked.status_code == 200
                assert liked.json() == {"reaction_count": 1, "liked_by_me": True}
            assert (
                client.get(encounter_url, headers=owner).json()["reaction_count"] == 1
            )
            assert (
                client.get(
                    f"/api/v1/animals/{first['animal_public_id']}", headers=owner
                ).json()["reaction_count"]
                == 1
            )
            for _ in range(2):
                removed = client.delete(like_url, headers=owner)
                assert removed.status_code == 200
                assert removed.json() == {"reaction_count": 0, "liked_by_me": False}
            with ThreadPoolExecutor(max_workers=2) as pool:
                concurrent = list(
                    pool.map(
                        lambda _: client.put(like_url, headers=owner).status_code,
                        range(2),
                    )
                )
            assert concurrent == [200, 200]
            current_detail = client.get(encounter_url, headers=owner).json()
            assert current_detail["reaction_count"] == 1

            collection = client.get("/api/v1/collection", headers=owner)
            assert collection.status_code == 200
            assert "private_location" not in collection.text
            cards = {item["animal_public_id"]: item for item in collection.json()}
            assert cards[first["animal_public_id"]]["own_encounter_count"] == 2
            assert cards[cat["animal_public_id"]]["own_encounter_count"] == 1
            assert len(cards) == 2
            profile = client.get("/api/v1/users/me/profile", headers=owner)
            assert profile.status_code == 200
            assert profile.json()["unique_animals"] == 2
            assert profile.json()["encounter_count"] == 3
            assert profile.json()["cats"] == 1
            assert profile.json()["dogs"] == 1
            assert "telegram_id" not in profile.text

            photo_url = f"/api/v1/photos/{first['photo_public_id']}/thumbnail"
            assert client.get(photo_url).status_code == 401
            assert client.get(photo_url, headers=owner).status_code == 200
            assert (
                client.get(photo_url, headers=owner).headers["content-type"]
                == "image/jpeg"
            )
            with get_engine().connect() as connection:
                precise = connection.execute(
                    text(
                        "SELECT ST_Y(private_location::geometry) "
                        "FROM encounters WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": first["encounter_public_id"]},
                ).scalar_one()
                assert precise == pytest.approx(41.7151)
                assert precise != first["approximate_latitude"]
    finally:
        app.dependency_overrides.clear()
