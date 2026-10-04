from collections.abc import Callable
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import exists, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pawspot.auth import get_internal_actor, get_public_actor
from pawspot.config import Settings, get_settings
from pawspot.db import get_session
from pawspot.models import Encounter, EncounterDraft, Photo, User
from pawspot.photos import InvalidPhoto, process_photo
from pawspot.schemas.workflow import (
    CandidateView,
    CollectionAnimal,
    CommitRequest,
    DraftPatch,
    DraftView,
    EncounterView,
)
from pawspot.services.encounters import (
    active_draft,
    commit_draft,
    draft_view,
    encounter_view,
    find_candidates,
    get_or_create_draft,
    own_collection,
    require_active_version,
    require_draft,
    update_draft,
)
from pawspot.storage import LocalPhotoStorage, PhotoStorage


def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> PhotoStorage:
    return LocalPhotoStorage(settings.media_dir)


def build_router(prefix: str, actor_dependency: Callable[..., User]) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["encounters"])
    Actor = Annotated[User, Depends(actor_dependency)]
    Database = Annotated[Session, Depends(get_session)]
    Configuration = Annotated[Settings, Depends(get_settings)]
    Storage = Annotated[PhotoStorage, Depends(get_storage)]

    @router.post("/encounter-drafts", response_model=DraftView)
    def create_draft(
        actor: Actor, session: Database, settings: Configuration
    ) -> DraftView:
        return get_or_create_draft(session, actor, settings)

    @router.get("/encounter-drafts/current", response_model=DraftView)
    def current_draft(
        actor: Actor, session: Database, settings: Configuration
    ) -> DraftView:
        draft = active_draft(session, actor, settings)
        if draft is None:
            raise HTTPException(status_code=404, detail="No active draft")
        return draft_view(draft)

    @router.get("/encounter-drafts/{draft_id}", response_model=DraftView)
    def get_draft(draft_id: UUID, actor: Actor, session: Database) -> DraftView:
        return draft_view(require_draft(session, actor, draft_id))

    @router.patch("/encounter-drafts/{draft_id}", response_model=DraftView)
    def patch_draft(
        draft_id: UUID,
        body: DraftPatch,
        actor: Actor,
        session: Database,
        settings: Configuration,
    ) -> DraftView:
        return update_draft(session, actor, draft_id, body, settings)

    @router.put("/encounter-drafts/{draft_id}/photo", response_model=DraftView)
    def upload_photo(
        draft_id: UUID,
        actor: Actor,
        session: Database,
        settings: Configuration,
        storage: Storage,
        expected_version: Annotated[int, Form(ge=0)],
        file: Annotated[UploadFile, File()],
    ) -> DraftView:
        draft = require_draft(session, actor, draft_id, lock=True)
        require_active_version(draft, expected_version)
        data = file.file.read(settings.max_photo_bytes + 1)
        try:
            processed = process_photo(data, file.content_type, settings.max_photo_bytes)
        except InvalidPhoto as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        photo_id = uuid4()
        main_key = f"{photo_id}/main.jpg"
        thumbnail_key = f"{photo_id}/thumbnail.jpg"
        try:
            storage.write(main_key, processed.main)
            storage.write(thumbnail_key, processed.thumbnail)
            session.add(
                Photo(
                    id=photo_id,
                    uploaded_by_user_id=actor.id,
                    storage_key=main_key,
                    thumbnail_key=thumbnail_key,
                    actual_mime="image/jpeg",
                    bytes=len(processed.main),
                    width=processed.width,
                    height=processed.height,
                )
            )
            draft.photo_id = photo_id
            draft.version += 1
            session.commit()
        except OSError, SQLAlchemyError:
            session.rollback()
            storage.delete(main_key)
            storage.delete(thumbnail_key)
            raise
        return draft_view(draft)

    @router.post(
        "/encounter-drafts/{draft_id}/matches", response_model=list[CandidateView]
    )
    def matches(
        draft_id: UUID, actor: Actor, session: Database, settings: Configuration
    ) -> list[CandidateView]:
        return find_candidates(session, actor, draft_id, settings)

    @router.post("/encounter-drafts/{draft_id}/commit", response_model=EncounterView)
    def commit(
        draft_id: UUID,
        body: CommitRequest,
        actor: Actor,
        session: Database,
        response: Response,
    ) -> EncounterView:
        result, created = commit_draft(session, actor, draft_id, body.expected_version)
        response.status_code = 201 if created else 200
        return result

    @router.delete("/encounter-drafts/{draft_id}", status_code=204)
    def cancel_draft(draft_id: UUID, actor: Actor, session: Database) -> None:
        draft = require_draft(session, actor, draft_id, lock=True)
        if draft.status == "committed":
            raise HTTPException(status_code=409, detail="Draft already committed")
        draft.status = "cancelled"
        draft.private_location = None
        draft.public_location = None
        draft.photo_id = None
        session.commit()

    @router.get("/encounters/{encounter_id}", response_model=EncounterView)
    def get_encounter(
        encounter_id: UUID, actor: Actor, session: Database
    ) -> EncounterView:
        return encounter_view(session, encounter_id)

    @router.get("/users/me/collection", response_model=list[CollectionAnimal])
    def collection(actor: Actor, session: Database) -> list[CollectionAnimal]:
        return own_collection(session, actor)

    @router.get("/photos/{photo_id}/{variant}")
    def get_photo(
        photo_id: UUID,
        variant: str,
        actor: Actor,
        session: Database,
        storage: Storage,
    ) -> Response:
        if variant not in {"main", "thumbnail"}:
            raise HTTPException(status_code=404, detail="Photo unavailable")
        photo = session.get(Photo, photo_id)
        if photo is None or photo.status != "ready":
            raise HTTPException(status_code=404, detail="Photo unavailable")
        attached = session.scalar(
            select(
                or_(
                    exists().where(
                        Encounter.photo_id == photo.id,
                        Encounter.deleted_at.is_(None),
                    ),
                    exists().where(
                        EncounterDraft.photo_id == photo.id,
                        EncounterDraft.user_id == actor.id,
                        EncounterDraft.status == "active",
                    ),
                )
            )
        )
        if not attached:
            raise HTTPException(status_code=404, detail="Photo unavailable")
        key = photo.storage_key if variant == "main" else photo.thumbnail_key
        try:
            data = storage.read(key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Photo unavailable") from exc
        return Response(
            data,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store"},
        )

    return router


public_router = build_router("/api/v1", get_public_actor)
internal_router = build_router("/internal/v1", get_internal_actor)
