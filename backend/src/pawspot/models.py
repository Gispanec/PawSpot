from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType


class GeographyPoint(UserDefinedType[object]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "geography(Point,4326)"


class GeometryPoint(UserDefinedType[object]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "geometry(Point,4326)"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)

    encounters: Mapped[list[Encounter]] = relationship(back_populates="user")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class City(TimestampMixin, Base):
    __tablename__ = "cities"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_cities_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_cities_longitude"),
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_cities_country_code"),
        CheckConstraint("selection_radius_m > 0", name="ck_cities_selection_radius"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_radius_m: Mapped[int] = mapped_column(Integer, nullable=False)

    encounters: Mapped[list[Encounter]] = relationship(back_populates="city")


class Animal(TimestampMixin, Base):
    __tablename__ = "animals"
    __table_args__ = (
        CheckConstraint("species IN ('cat', 'dog')", name="ck_animals_species"),
        CheckConstraint(
            "status IN ('active', 'merged', 'hidden')", name="ck_animals_status"
        ),
        CheckConstraint(
            "name IS NULL OR length(btrim(name)) > 0", name="ck_animals_name"
        ),
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id <> id",
            name="ck_animals_not_self_merged",
        ),
        CheckConstraint(
            "(status = 'merged') = (merged_into_id IS NOT NULL)",
            name="ck_animals_merge_status",
        ),
        Index("ix_animals_species_status", "species", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    species: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str | None] = mapped_column(String(80))
    primary_photo_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("photos.id", ondelete="RESTRICT")
    )
    city_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("cities.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    merged_into_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("animals.id", ondelete="RESTRICT")
    )

    encounters: Mapped[list[Encounter]] = relationship(back_populates="animal")


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (
        CheckConstraint("bytes > 0", name="ck_photos_bytes"),
        CheckConstraint("width > 0 AND height > 0", name="ck_photos_dimensions"),
        CheckConstraint("status IN ('ready', 'deleting')", name="ck_photos_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    thumbnail_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    actual_mime: Mapped[str] = mapped_column(String(80), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="ready"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Encounter(TimestampMixin, Base):
    __tablename__ = "encounters"
    __table_args__ = (
        CheckConstraint(
            "(public_location IS NULL) = (public_cell_key IS NULL) AND "
            "(public_location IS NULL) = (geo_policy_version IS NULL)",
            name="ck_encounters_public_geo_complete",
        ),
        CheckConstraint(
            "private_location IS NULL OR public_location IS NOT NULL",
            name="ck_encounters_private_requires_public",
        ),
        Index("ix_encounters_animal_observed", "animal_id", "observed_at", "id"),
        Index("ix_encounters_user_observed", "user_id", "observed_at", "id"),
        Index("ix_encounters_created", "created_at", "id"),
        Index(
            "ix_encounters_public_location_gist",
            "public_location",
            postgresql_using="gist",
            postgresql_where=text("deleted_at IS NULL AND public_location IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    animal_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("animals.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    photo_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("photos.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    city_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )
    private_location: Mapped[object | None] = mapped_column(GeographyPoint())
    public_location: Mapped[object | None] = mapped_column(GeographyPoint())
    public_cell_key: Mapped[str | None] = mapped_column(String(80))
    geo_policy_version: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    animal: Mapped[Animal] = relationship(back_populates="encounters")
    user: Mapped[User] = relationship(back_populates="encounters")
    city: Mapped[City] = relationship(back_populates="encounters")


class EncounterDraft(TimestampMixin, Base):
    __tablename__ = "encounter_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'committed', 'cancelled')",
            name="ck_encounter_drafts_status",
        ),
        CheckConstraint(
            "species IS NULL OR species IN ('cat', 'dog')",
            name="ck_encounter_drafts_species",
        ),
        CheckConstraint(
            "selection IS NULL OR selection IN ('new', 'existing')",
            name="ck_encounter_drafts_selection",
        ),
        CheckConstraint("version >= 0", name="ck_encounter_drafts_version"),
        Index(
            "uq_encounter_drafts_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    photo_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("photos.id", ondelete="RESTRICT")
    )
    species: Mapped[str | None] = mapped_column(String(24))
    location_set: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    private_location: Mapped[object | None] = mapped_column(GeographyPoint())
    public_location: Mapped[object | None] = mapped_column(GeographyPoint())
    public_cell_key: Mapped[str | None] = mapped_column(String(80))
    geo_policy_version: Mapped[int | None] = mapped_column(Integer)
    city_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("cities.id", ondelete="RESTRICT")
    )
    selection: Mapped[str | None] = mapped_column(String(16))
    animal_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("animals.id", ondelete="RESTRICT")
    )
    new_name: Mapped[str | None] = mapped_column(String(80))
    comment: Mapped[str | None] = mapped_column(String(500))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    result_encounter_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("encounters.id", ondelete="RESTRICT"), unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Reaction(Base):
    __tablename__ = "reactions"
    __table_args__ = (
        CheckConstraint(
            "reaction_type IN ('heart', 'laugh', 'love')", name="ck_reactions_type"
        ),
        UniqueConstraint(
            "user_id",
            "encounter_id",
            "reaction_type",
            name="uq_reactions_actor_target_type",
        ),
        Index("ix_reactions_encounter", "encounter_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    encounter_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("encounters.id", ondelete="RESTRICT"), nullable=False
    )
    reaction_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
