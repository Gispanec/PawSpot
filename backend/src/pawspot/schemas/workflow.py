from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LocationInput(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class NewAnimalInput(BaseModel):
    name: str | None = Field(default=None, max_length=80)


class DraftPatch(BaseModel):
    expected_version: int = Field(ge=0)
    species: Literal["cat", "dog"] | None = None
    location: LocationInput | None = None
    city_public_id: UUID | None = None
    animal_public_id: UUID | None = None
    new_animal: NewAnimalInput | None = None
    comment: str | None = Field(default=None, max_length=500)
    observed_at: datetime | None = None


class DraftView(BaseModel):
    public_id: UUID
    version: int
    state: str
    photo_uploaded: bool
    species: str | None
    location_present: bool
    city_public_id: UUID | None
    selection: str | None
    animal_public_id: UUID | None
    new_name: str | None
    comment: str | None
    expires_at: datetime


class CandidateView(BaseModel):
    animal_public_id: UUID
    name: str | None
    species: str
    thumbnail_photo_id: UUID | None


class EncounterView(BaseModel):
    encounter_public_id: UUID
    animal_public_id: UUID
    animal_name: str | None
    species: str
    author_name: str
    city_name: str
    photo_public_id: UUID
    comment: str | None
    observed_at: datetime
    approximate_latitude: float | None
    approximate_longitude: float | None


class CommitRequest(BaseModel):
    expected_version: int = Field(ge=0)


class CollectionAnimal(BaseModel):
    public_id: UUID
    name: str | None
    species: str
