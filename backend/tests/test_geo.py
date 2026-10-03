from datetime import UTC, datetime
from uuid import uuid4

import pytest

from pawspot.geo import point_wkt, public_location
from pawspot.schemas.encounter import EncounterPublic


def test_public_point_is_stable_and_shared_within_cell() -> None:
    first = public_location(41.7151, 44.8271, 200)
    assert first == public_location(41.7151, 44.8271, 200)
    assert first == public_location(first.latitude, first.longitude, 200)
    assert first.cell_key.startswith("v1:200:")
    assert first.latitude != 41.7151 or first.longitude != 44.8271
    assert first == public_location(first.latitude + 0.0001, first.longitude, 200)
    assert public_location(0, -180, 200) == public_location(0, 180, 200)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(-90, -180), (90, 180), (0, 180), (0, -180), (51.5074, -0.1278)],
)
def test_public_point_stays_in_valid_coordinate_bounds(
    latitude: float, longitude: float
) -> None:
    point = public_location(latitude, longitude, 200)
    assert -90 <= point.latitude <= 90
    assert -180 <= point.longitude <= 180


@pytest.mark.parametrize(
    ("latitude", "longitude", "radius"),
    [(91, 0, 200), (0, -181, 200), (float("nan"), 0, 200), (0, 0, 0)],
)
def test_invalid_geo_is_rejected(
    latitude: float, longitude: float, radius: int
) -> None:
    with pytest.raises(ValueError):
        public_location(latitude, longitude, radius)


def test_public_dto_has_no_precise_location_even_for_author() -> None:
    point = public_location(41.7151, 44.8271, 200)
    dto = EncounterPublic(
        public_id=uuid4(),
        animal_public_id=uuid4(),
        author_public_id=uuid4(),
        city_public_id=uuid4(),
        observed_at=datetime.now(UTC),
        comment=None,
        approximate_location=point,
    )
    data = dto.model_dump(mode="json")
    assert "private_location" not in data
    assert "latitude" not in data
    assert data["approximate_location"]["latitude"] == point.latitude
    assert data["approximate_location"]["longitude"] == point.longitude
    with pytest.raises(ValueError):
        EncounterPublic.model_validate(
            {**data, "private_location": point_wkt(41.7151, 44.8271)}
        )
