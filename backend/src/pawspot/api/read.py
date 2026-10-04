import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from pawspot.auth import get_public_actor
from pawspot.db import get_session
from pawspot.models import Animal, City, Encounter, GeometryPoint, Reaction, User

router = APIRouter(prefix="/api/v1", tags=["explore"])
Actor = Annotated[User, Depends(get_public_actor)]
Database = Annotated[Session, Depends(get_session)]


class EncounterCard(BaseModel):
    public_id: UUID
    animal_public_id: UUID
    animal_name: str | None
    species: str
    photo_public_id: UUID
    author_name: str
    city_name: str
    comment: str | None
    observed_at: datetime
    approximate_latitude: float | None
    approximate_longitude: float | None
    reaction_count: int
    liked_by_me: bool
    is_mine: bool


class EncounterPage(BaseModel):
    items: list[EncounterCard]
    next_cursor: str | None


class AnimalDetail(BaseModel):
    public_id: UUID
    name: str | None
    species: str
    city_name: str | None
    primary_photo_public_id: UUID | None
    encounter_count: int
    photo_count: int
    observer_count: int
    reaction_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    created_by_name: str
    timeline: EncounterPage


CARD_FIELDS = tuple(EncounterCard.model_fields)


def _card(row: Sequence[object]) -> EncounterCard:
    return EncounterCard.model_validate(dict(zip(CARD_FIELDS, row, strict=False)))


def _encode_cursor(value: datetime, identifier: UUID) -> str:
    payload = json.dumps([value.isoformat(), str(identifier)], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    if len(cursor) > 256:
        raise HTTPException(status_code=422, detail="Invalid cursor")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value, identifier = json.loads(raw)
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            raise ValueError("Naive cursor")
        return moment, UUID(identifier)
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc


def _card_columns(actor: User) -> tuple[Any, ...]:
    reaction_count = (
        select(func.count(Reaction.id))
        .where(Reaction.encounter_id == Encounter.id, Reaction.reaction_type == "heart")
        .correlate(Encounter)
        .scalar_subquery()
    )
    liked = exists().where(
        Reaction.encounter_id == Encounter.id,
        Reaction.user_id == actor.id,
        Reaction.reaction_type == "heart",
    )
    return (
        Encounter.id,
        Animal.id,
        Animal.name,
        Animal.species,
        Encounter.photo_id,
        User.display_name,
        City.name,
        Encounter.comment,
        Encounter.observed_at,
        func.ST_Y(cast(Encounter.public_location, GeometryPoint())),
        func.ST_X(cast(Encounter.public_location, GeometryPoint())),
        reaction_count,
        liked,
        Encounter.user_id == actor.id,
    )


def _cards(
    session: Session,
    actor: User,
    *,
    animal_id: UUID | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> EncounterPage:
    ordering = Encounter.observed_at if animal_id else Encounter.created_at
    statement = (
        select(*_card_columns(actor), ordering)
        .join(Animal, Animal.id == Encounter.animal_id)
        .join(User, User.id == Encounter.user_id)
        .join(City, City.id == Encounter.city_id)
        .where(Encounter.deleted_at.is_(None), Animal.status == "active")
        .order_by(ordering.desc(), Encounter.id.desc())
        .limit(limit + 1)
    )
    if animal_id is not None:
        statement = statement.where(Animal.id == animal_id)
    if cursor:
        moment, identifier = _decode_cursor(cursor)
        statement = statement.where(
            or_(ordering < moment, and_(ordering == moment, Encounter.id < identifier))
        )
    rows = session.execute(statement).all()
    visible = rows[:limit]
    return EncounterPage(
        items=[_card(row[:14]) for row in visible],
        next_cursor=(
            _encode_cursor(visible[-1][14], visible[-1][0])
            if len(rows) > limit and visible
            else None
        ),
    )


@router.get("/feed", response_model=EncounterPage)
def feed(
    actor: Actor,
    session: Database,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> EncounterPage:
    return _cards(session, actor, limit=limit, cursor=cursor)


@router.get("/animals/{animal_id}", response_model=AnimalDetail)
def animal_detail(
    animal_id: UUID,
    actor: Actor,
    session: Database,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> AnimalDetail:
    animal = session.get(Animal, animal_id)
    if animal is None or animal.status != "active":
        raise HTTPException(status_code=404, detail="Animal unavailable")
    city_name = session.scalar(select(City.name).where(City.id == animal.city_id))
    creator_name = session.scalar(
        select(User.display_name).where(User.id == animal.created_by_user_id)
    )
    stats = session.execute(
        select(
            func.count(Encounter.id),
            func.count(func.distinct(Encounter.photo_id)),
            func.count(func.distinct(Encounter.user_id)),
            func.min(Encounter.observed_at),
            func.max(Encounter.observed_at),
        ).where(Encounter.animal_id == animal_id, Encounter.deleted_at.is_(None))
    ).one()
    reactions = session.scalar(
        select(func.count(Reaction.id))
        .join(Encounter, Encounter.id == Reaction.encounter_id)
        .where(
            Encounter.animal_id == animal_id,
            Encounter.deleted_at.is_(None),
            Reaction.reaction_type == "heart",
        )
    )
    return AnimalDetail(
        public_id=animal.id,
        name=animal.name,
        species=animal.species,
        city_name=city_name,
        primary_photo_public_id=animal.primary_photo_id,
        encounter_count=stats[0],
        photo_count=stats[1],
        observer_count=stats[2],
        reaction_count=reactions or 0,
        first_observed_at=stats[3],
        last_observed_at=stats[4],
        created_by_name=creator_name or "Участник PawSpot",
        timeline=_cards(
            session, actor, animal_id=animal_id, limit=limit, cursor=cursor
        ),
    )


@router.get("/encounters/{encounter_id}/detail", response_model=EncounterCard)
def encounter_detail(
    encounter_id: UUID, actor: Actor, session: Database
) -> EncounterCard:
    row = session.execute(
        select(*_card_columns(actor))
        .join(Animal, Animal.id == Encounter.animal_id)
        .join(User, User.id == Encounter.user_id)
        .join(City, City.id == Encounter.city_id)
        .where(
            Encounter.id == encounter_id,
            Encounter.deleted_at.is_(None),
            Animal.status == "active",
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Encounter unavailable")
    return _card(row)
