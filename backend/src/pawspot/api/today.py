from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from pawspot.auth import get_internal_actor
from pawspot.config import Settings, get_settings
from pawspot.db import get_session
from pawspot.models import Animal, Encounter, User
from pawspot.services.encounters import get_default_city

router = APIRouter(prefix="/internal/v1", tags=["internal-feed"])


class TodayCard(BaseModel):
    encounter_public_id: UUID
    animal_public_id: UUID
    animal_name: str | None
    species: str
    photo_public_id: UUID
    observed_at: datetime
    comment: str | None
    city_name: str
    location_present: bool
    repeat_encounter: bool


class TodayFeed(BaseModel):
    timezone: str
    encounters: int
    dogs: int
    cats: int
    first_animals: int
    page: int
    page_size: int
    items: list[TodayCard]


@router.get("/feed/today", response_model=TodayFeed)
def today_feed(
    actor: Annotated[User, Depends(get_internal_actor)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=5)] = 1,
) -> TodayFeed:
    city = get_default_city(session, settings)
    zone = ZoneInfo(city.timezone)
    local_day = datetime.now(zone).date()
    start = datetime.combine(local_day, time.min, zone).astimezone(UTC)
    end = datetime.combine(local_day + timedelta(days=1), time.min, zone).astimezone(
        UTC
    )
    conditions = (
        Encounter.city_id == city.id,
        Encounter.deleted_at.is_(None),
        Encounter.observed_at >= start,
        Encounter.observed_at < end,
        Animal.status == "active",
    )
    totals = session.execute(
        select(
            func.count(Encounter.id),
            func.count(case((Animal.species == "dog", 1))),
            func.count(case((Animal.species == "cat", 1))),
        )
        .join(Animal, Animal.id == Encounter.animal_id)
        .where(*conditions)
    ).one()
    first_animals = (
        session.scalar(
            select(func.count(Animal.id)).where(
                Animal.city_id == city.id,
                Animal.status == "active",
                Animal.created_at >= start,
                Animal.created_at < end,
            )
        )
        or 0
    )
    earlier = Encounter.__table__.alias("earlier")
    repeat = exists(
        select(earlier.c.id).where(
            earlier.c.animal_id == Encounter.animal_id,
            earlier.c.deleted_at.is_(None),
            or_(
                earlier.c.observed_at < Encounter.observed_at,
                (earlier.c.observed_at == Encounter.observed_at)
                & (earlier.c.id < Encounter.id),
            ),
        )
    )
    rows = session.execute(
        select(
            Encounter.id,
            Animal.id,
            Animal.name,
            Animal.species,
            Encounter.photo_id,
            Encounter.observed_at,
            Encounter.comment,
            literal(city.name),
            Encounter.public_location.is_not(None),
            repeat,
        )
        .join(Animal, Animal.id == Encounter.animal_id)
        .where(*conditions)
        .order_by(Encounter.observed_at.desc(), Encounter.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TodayFeed(
        timezone=city.timezone,
        encounters=totals[0],
        dogs=totals[1],
        cats=totals[2],
        first_animals=first_animals,
        page=page,
        page_size=page_size,
        items=[
            TodayCard.model_validate(
                dict(zip(TodayCard.model_fields, row, strict=True))
            )
            for row in rows
        ],
    )
