from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pawspot.auth import get_public_actor
from pawspot.db import get_session
from pawspot.models import Animal, City, Encounter, GeometryPoint, Reaction, User

router = APIRouter(prefix="/api/v1", tags=["explore"])
Actor = Annotated[User, Depends(get_public_actor)]
Database = Annotated[Session, Depends(get_session)]


class MapMarker(BaseModel):
    animal_public_id: UUID
    name: str | None
    species: str
    photo_public_id: UUID
    encounter_public_id: UUID
    encounter_count: int
    last_observed_at: datetime
    city_name: str
    approximate_latitude: float
    approximate_longitude: float


class ReactionState(BaseModel):
    reaction_count: int
    liked_by_me: bool


class CollectionCard(BaseModel):
    animal_public_id: UUID
    name: str | None
    species: str
    photo_public_id: UUID | None
    own_encounter_count: int
    last_observed_at: datetime


class ProfileView(BaseModel):
    display_name: str
    joined_at: datetime
    unique_animals: int
    encounter_count: int
    cats: int
    dogs: int


@router.get("/map/animals", response_model=list[MapMarker])
def map_animals(
    actor: Actor,
    session: Database,
    south: Annotated[float, Query(ge=-90, le=90)],
    west: Annotated[float, Query(ge=-180, le=180)],
    north: Annotated[float, Query(ge=-90, le=90)],
    east: Annotated[float, Query(ge=-180, le=180)],
    species: Literal["cat", "dog"] | None = None,
) -> list[MapMarker]:
    if south >= north or west >= east or north - south > 2 or east - west > 2:
        raise HTTPException(status_code=422, detail="Choose a smaller map area")
    located = (
        select(
            Encounter.id.label("encounter_id"),
            Encounter.animal_id,
            Encounter.photo_id,
            Encounter.city_id,
            Encounter.observed_at,
            func.ST_Y(cast(Encounter.public_location, GeometryPoint())).label("lat"),
            func.ST_X(cast(Encounter.public_location, GeometryPoint())).label("lon"),
            func.row_number()
            .over(
                partition_by=Encounter.animal_id,
                order_by=(Encounter.observed_at.desc(), Encounter.id.desc()),
            )
            .label("rank"),
        )
        .where(
            Encounter.deleted_at.is_(None),
            Encounter.public_location.is_not(None),
        )
        .subquery()
    )
    count = (
        select(func.count(Encounter.id))
        .where(Encounter.animal_id == Animal.id, Encounter.deleted_at.is_(None))
        .correlate(Animal)
        .scalar_subquery()
    )
    statement = (
        select(
            Animal.id,
            Animal.name,
            Animal.species,
            located.c.photo_id,
            located.c.encounter_id,
            count,
            located.c.observed_at,
            City.name,
            located.c.lat,
            located.c.lon,
        )
        .join(located, located.c.animal_id == Animal.id)
        .join(City, City.id == located.c.city_id)
        .where(
            located.c.rank == 1,
            Animal.status == "active",
            located.c.lat >= south,
            located.c.lat <= north,
            located.c.lon >= west,
            located.c.lon <= east,
        )
        .order_by(located.c.observed_at.desc(), Animal.id.desc())
        .limit(200)
    )
    if species is not None:
        statement = statement.where(Animal.species == species)
    return [
        MapMarker.model_validate(dict(zip(MapMarker.model_fields, row, strict=True)))
        for row in session.execute(statement)
    ]


def _target(session: Session, encounter_id: UUID) -> Encounter:
    encounter = session.scalar(
        select(Encounter)
        .join(Animal, Animal.id == Encounter.animal_id)
        .where(
            Encounter.id == encounter_id,
            Encounter.deleted_at.is_(None),
            Animal.status == "active",
        )
    )
    if encounter is None:
        raise HTTPException(status_code=404, detail="Encounter unavailable")
    return encounter


def _reaction_state(session: Session, actor: User, encounter_id: UUID) -> ReactionState:
    rows = (
        session.execute(
            select(Reaction.user_id).where(
                Reaction.encounter_id == encounter_id,
                Reaction.reaction_type == "heart",
            )
        )
        .scalars()
        .all()
    )
    return ReactionState(reaction_count=len(rows), liked_by_me=actor.id in rows)


@router.put("/encounters/{encounter_id}/like", response_model=ReactionState)
def add_like(encounter_id: UUID, actor: Actor, session: Database) -> ReactionState:
    encounter = _target(session, encounter_id)
    if encounter.user_id == actor.id:
        raise HTTPException(status_code=403, detail="Cannot react to your encounter")
    session.execute(
        insert(Reaction)
        .values(
            id=uuid4(),
            user_id=actor.id,
            encounter_id=encounter_id,
            reaction_type="heart",
        )
        .on_conflict_do_nothing(constraint="uq_reactions_actor_target_type")
    )
    session.commit()
    return _reaction_state(session, actor, encounter_id)


@router.delete("/encounters/{encounter_id}/like", response_model=ReactionState)
def remove_like(encounter_id: UUID, actor: Actor, session: Database) -> ReactionState:
    _target(session, encounter_id)
    reaction = session.scalar(
        select(Reaction).where(
            Reaction.encounter_id == encounter_id,
            Reaction.user_id == actor.id,
            Reaction.reaction_type == "heart",
        )
    )
    if reaction is not None:
        session.delete(reaction)
        session.commit()
    return _reaction_state(session, actor, encounter_id)


@router.get("/collection", response_model=list[CollectionCard])
def collection(actor: Actor, session: Database) -> list[CollectionCard]:
    statement = (
        select(
            Animal.id,
            Animal.name,
            Animal.species,
            Animal.primary_photo_id,
            func.count(Encounter.id),
            func.max(Encounter.observed_at),
        )
        .join(Encounter, Encounter.animal_id == Animal.id)
        .where(
            Encounter.user_id == actor.id,
            Encounter.deleted_at.is_(None),
            Animal.status == "active",
        )
        .group_by(Animal.id)
        .order_by(func.max(Encounter.observed_at).desc(), Animal.id.desc())
        .limit(200)
    )
    return [
        CollectionCard.model_validate(
            dict(zip(CollectionCard.model_fields, row, strict=True))
        )
        for row in session.execute(statement)
    ]


@router.get("/users/me/profile", response_model=ProfileView)
def profile(actor: Actor, session: Database) -> ProfileView:
    counts = session.execute(
        select(
            func.count(Encounter.id),
            func.count(func.distinct(Animal.id)),
            func.count(func.distinct(Animal.id)).filter(Animal.species == "cat"),
            func.count(func.distinct(Animal.id)).filter(Animal.species == "dog"),
        )
        .join(Animal, Animal.id == Encounter.animal_id)
        .where(
            Encounter.user_id == actor.id,
            Encounter.deleted_at.is_(None),
            Animal.status == "active",
        )
    ).one()
    return ProfileView(
        display_name=actor.display_name,
        joined_at=actor.created_at,
        encounter_count=counts[0],
        unique_animals=counts[1],
        cats=counts[2],
        dogs=counts[3],
    )
