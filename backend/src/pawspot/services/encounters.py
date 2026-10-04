from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pawspot.config import Settings
from pawspot.geo import point_wkt, public_location
from pawspot.models import (
    Animal,
    City,
    Encounter,
    EncounterDraft,
    GeometryPoint,
    Photo,
    User,
)
from pawspot.schemas.workflow import (
    CandidateView,
    CollectionAnimal,
    DraftPatch,
    DraftView,
    EncounterView,
)


def draft_state(draft: EncounterDraft) -> str:
    if draft.status != "active":
        return draft.status
    if draft.photo_id is None:
        return "need_photo"
    if draft.species is None:
        return "need_species"
    if not draft.location_set:
        return "need_location_or_skip"
    if draft.selection is None:
        return "choose_animal"
    return "ready"


def draft_view(session: Session, draft: EncounterDraft) -> DraftView:
    animal = session.get(Animal, draft.animal_id) if draft.animal_id else None
    city = session.get(City, draft.city_id) if draft.city_id else None
    return DraftView(
        public_id=draft.id,
        version=draft.version,
        state=draft_state(draft),
        photo_uploaded=draft.photo_id is not None,
        photo_public_id=draft.photo_id,
        species=draft.species,
        location_present=draft.public_location is not None,
        city_public_id=draft.city_id,
        selection=draft.selection,
        animal_public_id=draft.animal_id,
        selected_animal_name=animal.name if animal else None,
        new_name=draft.new_name,
        comment=draft.comment,
        city_name=city.name if city else None,
        expires_at=draft.expires_at,
    )


def get_default_city(session: Session, settings: Settings) -> City:
    city = session.scalar(select(City).where(City.slug == settings.default_city_slug))
    if city is None:
        raise HTTPException(status_code=503, detail="Default city unavailable")
    return city


def active_draft(
    session: Session, actor: User, settings: Settings
) -> EncounterDraft | None:
    draft = session.scalar(
        select(EncounterDraft).where(
            EncounterDraft.user_id == actor.id,
            EncounterDraft.status == "active",
        )
    )
    if draft is not None and draft.expires_at <= datetime.now(UTC):
        draft.status = "cancelled"
        draft.private_location = None
        draft.public_location = None
        draft.photo_id = None
        session.commit()
        return None
    return draft


def get_or_create_draft(session: Session, actor: User, settings: Settings) -> DraftView:
    draft = active_draft(session, actor, settings)
    if draft is not None:
        return draft_view(session, draft)
    city = get_default_city(session, settings)
    draft = EncounterDraft(
        user_id=actor.id,
        city_id=city.id,
        observed_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.draft_ttl_hours),
    )
    try:
        with session.begin_nested():
            session.add(draft)
            session.flush()
        session.commit()
    except IntegrityError:
        session.rollback()
        concurrent = active_draft(session, actor, settings)
        if concurrent is None:
            raise
        return draft_view(session, concurrent)
    return draft_view(session, draft)


def require_draft(
    session: Session, actor: User, draft_id: UUID, *, lock: bool = False
) -> EncounterDraft:
    query = select(EncounterDraft).where(
        EncounterDraft.id == draft_id, EncounterDraft.user_id == actor.id
    )
    if lock:
        query = query.with_for_update()
    draft = session.scalar(query)
    if draft is None or draft.status == "cancelled":
        raise HTTPException(status_code=404, detail="Draft unavailable")
    if draft.status == "active" and draft.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Draft expired")
    return draft


def require_active_version(draft: EncounterDraft, expected_version: int) -> None:
    if draft.status != "active":
        raise HTTPException(status_code=409, detail="Draft already committed")
    if draft.version != expected_version:
        raise HTTPException(status_code=409, detail="Draft version changed")


def update_draft(
    session: Session, actor: User, draft_id: UUID, patch: DraftPatch, settings: Settings
) -> DraftView:
    draft = require_draft(session, actor, draft_id, lock=True)
    require_active_version(draft, patch.expected_version)
    fields = patch.model_fields_set
    if "species" in fields:
        if patch.species is None:
            raise HTTPException(status_code=422, detail="Species required")
        if draft.species != patch.species:
            draft.selection = None
            draft.animal_id = None
            draft.new_name = None
        draft.species = patch.species
    if "city_public_id" in fields:
        if patch.city_public_id is None:
            raise HTTPException(status_code=422, detail="City required")
        city = session.get(City, patch.city_public_id)
        if city is None:
            raise HTTPException(status_code=422, detail="Unknown city")
        draft.city_id = city.id
    if "location" in fields:
        draft.location_set = True
        if patch.location is None:
            draft.private_location = None
            draft.public_location = None
            draft.public_cell_key = None
            draft.geo_policy_version = None
        else:
            point = public_location(
                patch.location.latitude,
                patch.location.longitude,
                settings.public_location_radius_m,
            )
            draft.private_location = func.ST_GeogFromText(
                point_wkt(patch.location.latitude, patch.location.longitude)
            )
            draft.public_location = func.ST_GeogFromText(
                point_wkt(point.latitude, point.longitude)
            )
            draft.public_cell_key = point.cell_key
            draft.geo_policy_version = point.policy_version
    if "location" in fields or "city_public_id" in fields:
        if patch.location is not None:
            city = session.get(City, draft.city_id)
            if city is None:
                raise HTTPException(status_code=422, detail="City required")
            city_center = func.ST_GeogFromText(point_wkt(city.latitude, city.longitude))
            location = func.ST_GeogFromText(
                point_wkt(patch.location.latitude, patch.location.longitude)
            )
            if not session.scalar(
                select(func.ST_DWithin(location, city_center, city.selection_radius_m))
            ):
                raise HTTPException(status_code=422, detail="Choose a supported city")
    if "animal_public_id" in fields and "new_animal" in fields:
        raise HTTPException(status_code=422, detail="Choose one animal option")
    if "animal_public_id" in fields:
        animal = session.get(Animal, patch.animal_public_id)
        if (
            animal is None
            or animal.status != "active"
            or animal.species != draft.species
        ):
            raise HTTPException(status_code=422, detail="Animal unavailable")
        draft.selection = "existing"
        draft.animal_id = animal.id
        draft.new_name = None
    if "new_animal" in fields:
        if patch.new_animal is None:
            raise HTTPException(status_code=422, detail="New animal required")
        draft.selection = "new"
        draft.animal_id = None
        draft.new_name = (
            patch.new_animal.name.strip() or None if patch.new_animal.name else None
        )
    if "comment" in fields:
        draft.comment = patch.comment.strip() or None if patch.comment else None
    if "observed_at" in fields:
        if patch.observed_at is None or patch.observed_at.tzinfo is None:
            raise HTTPException(
                status_code=422, detail="Observed time must have timezone"
            )
        if patch.observed_at > datetime.now(UTC) + timedelta(minutes=5):
            raise HTTPException(
                status_code=422, detail="Observed time is in the future"
            )
        draft.observed_at = patch.observed_at
    draft.version += 1
    session.commit()
    return draft_view(session, draft)


def find_candidates(
    session: Session, actor: User, draft_id: UUID, settings: Settings
) -> list[CandidateView]:
    draft = require_draft(session, actor, draft_id)
    if draft.species is None or draft.public_location is None:
        return []
    cutoff = datetime.now(UTC) - timedelta(days=settings.matching_lookback_days)
    query_public = (
        select(EncounterDraft.public_location)
        .where(EncounterDraft.id == draft.id)
        .scalar_subquery()
    )
    distance = func.ST_Distance(Encounter.public_location, query_public)
    rows = session.execute(
        select(Animal, Encounter.observed_at, distance)
        .join(Encounter, Encounter.animal_id == Animal.id)
        .where(
            Animal.species == draft.species,
            Animal.status == "active",
            Encounter.deleted_at.is_(None),
            Encounter.public_location.is_not(None),
            Encounter.observed_at >= cutoff,
            func.ST_DWithin(
                Encounter.public_location, query_public, settings.matching_radius_m
            ),
        )
    ).all()
    best: dict[UUID, tuple[float, Animal]] = {}
    now = datetime.now(UTC)
    for animal, observed_at, distance_m in rows:
        age_days = max(0.0, (now - observed_at).total_seconds() / 86400)
        score = (
            distance_m / settings.matching_radius_m
            + age_days / settings.matching_lookback_days
        )
        if animal.id not in best or score < best[animal.id][0]:
            best[animal.id] = (score, animal)
    ranked = sorted(best.values(), key=lambda item: (item[0], str(item[1].id)))
    top = [animal for _, animal in ranked[: settings.matching_max_candidates]]
    if not top:
        return []
    stats = {
        animal_id: (last_seen, count)
        for animal_id, last_seen, count in session.execute(
            select(
                Encounter.animal_id,
                func.max(Encounter.observed_at),
                func.count(Encounter.id),
            )
            .where(
                Encounter.animal_id.in_([animal.id for animal in top]),
                Encounter.deleted_at.is_(None),
            )
            .group_by(Encounter.animal_id)
        )
    }
    return [
        CandidateView(
            animal_public_id=animal.id,
            name=animal.name,
            species=animal.species,
            thumbnail_photo_id=animal.primary_photo_id,
            last_observed_at=stats[animal.id][0],
            encounter_count=stats[animal.id][1],
        )
        for animal in top
    ]


def commit_draft(
    session: Session, actor: User, draft_id: UUID, expected_version: int
) -> tuple[EncounterView, bool]:
    draft = require_draft(session, actor, draft_id, lock=True)
    if draft.status == "committed" and draft.result_encounter_id is not None:
        return encounter_view(session, draft.result_encounter_id), False
    require_active_version(draft, expected_version)
    if draft_state(draft) != "ready" or draft.city_id is None or draft.photo_id is None:
        raise HTTPException(status_code=409, detail="Draft is incomplete")
    photo = session.get(Photo, draft.photo_id)
    if (
        photo is None
        or photo.uploaded_by_user_id != actor.id
        or photo.status != "ready"
    ):
        raise HTTPException(status_code=409, detail="Photo unavailable")
    if draft.selection == "new":
        animal = Animal(
            species=draft.species,
            name=draft.new_name,
            city_id=draft.city_id,
            created_by_user_id=actor.id,
            primary_photo_id=photo.id,
        )
        session.add(animal)
        session.flush()
    else:
        existing_animal = session.get(Animal, draft.animal_id, with_for_update=True)
        if (
            existing_animal is None
            or existing_animal.status != "active"
            or existing_animal.species != draft.species
        ):
            raise HTTPException(status_code=409, detail="Animal unavailable")
        animal = existing_animal
    encounter = Encounter(
        animal_id=animal.id,
        user_id=actor.id,
        photo_id=photo.id,
        city_id=draft.city_id,
        private_location=select(EncounterDraft.private_location)
        .where(EncounterDraft.id == draft.id)
        .scalar_subquery(),
        public_location=select(EncounterDraft.public_location)
        .where(EncounterDraft.id == draft.id)
        .scalar_subquery(),
        public_cell_key=draft.public_cell_key,
        geo_policy_version=draft.geo_policy_version,
        comment=draft.comment,
        observed_at=draft.observed_at,
    )
    session.add(encounter)
    session.flush()
    draft.status = "committed"
    draft.result_encounter_id = encounter.id
    draft.private_location = None
    draft.public_location = None
    draft.version += 1
    session.commit()
    return encounter_view(session, encounter.id), True


def encounter_view(session: Session, encounter_id: UUID) -> EncounterView:
    row = session.execute(
        select(
            Encounter,
            Animal,
            User.display_name,
            City.name,
            func.ST_Y(cast(Encounter.public_location, GeometryPoint())),
            func.ST_X(cast(Encounter.public_location, GeometryPoint())),
        )
        .join(Animal, Animal.id == Encounter.animal_id)
        .join(User, User.id == Encounter.user_id)
        .join(City, City.id == Encounter.city_id)
        .where(Encounter.id == encounter_id, Encounter.deleted_at.is_(None))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Encounter unavailable")
    encounter, animal, author_name, city_name, latitude, longitude = row
    return EncounterView(
        encounter_public_id=encounter.id,
        animal_public_id=animal.id,
        animal_name=animal.name,
        species=animal.species,
        author_name=author_name,
        city_name=city_name,
        photo_public_id=encounter.photo_id,
        comment=encounter.comment,
        observed_at=encounter.observed_at,
        approximate_latitude=latitude,
        approximate_longitude=longitude,
    )


def own_collection(session: Session, actor: User) -> list[CollectionAnimal]:
    animals = session.scalars(
        select(Animal)
        .join(Encounter, Encounter.animal_id == Animal.id)
        .where(
            Encounter.user_id == actor.id,
            Encounter.deleted_at.is_(None),
            Animal.status == "active",
        )
        .distinct()
        .limit(50)
    ).all()
    return [
        CollectionAnimal(
            public_id=a.id,
            name=a.name,
            species=a.species,
            thumbnail_photo_id=a.primary_photo_id,
        )
        for a in animals
    ]
