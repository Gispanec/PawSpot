from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from pawspot.config import Settings
from pawspot.models import Encounter, EncounterDraft, Photo
from pawspot.storage import PhotoStorage


def cleanup_expired_drafts_and_photos(
    session: Session, storage: PhotoStorage, settings: Settings
) -> int:
    now = datetime.now(UTC)
    session.execute(
        update(EncounterDraft)
        .where(EncounterDraft.status == "active", EncounterDraft.expires_at < now)
        .values(
            status="cancelled",
            photo_id=None,
            private_location=None,
            public_location=None,
        )
    )
    session.commit()

    cutoff = now - timedelta(hours=settings.photo_cleanup_grace_hours)
    orphans = session.scalars(
        select(Photo).where(
            Photo.created_at < cutoff,
            ~exists().where(Encounter.photo_id == Photo.id),
            ~exists().where(EncounterDraft.photo_id == Photo.id),
        )
    ).all()
    for photo in orphans:
        storage.delete(photo.storage_key)
        storage.delete(photo.thumbnail_key)
        session.delete(photo)
    session.commit()
    return len(orphans)
