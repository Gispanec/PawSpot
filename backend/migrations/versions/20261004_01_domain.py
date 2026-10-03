"""Initial domain schema and Tbilisi seed."""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision: str = "20261004_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TBILISI_ID = "477e4be6-8496-4a61-96a7-10db03781b0e"


class GeographyPoint(UserDefinedType[object]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "geography(Point,4326)"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(64)),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
    )
    op.create_table(
        "cities",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("selection_radius_m", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_cities_slug"),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_cities_latitude"),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180", name="ck_cities_longitude"
        ),
        sa.CheckConstraint(
            "country_code ~ '^[A-Z]{2}$'", name="ck_cities_country_code"
        ),
        sa.CheckConstraint("selection_radius_m > 0", name="ck_cities_selection_radius"),
    )
    op.create_table(
        "photos",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "uploaded_by_user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(255), nullable=False, unique=True),
        sa.Column("thumbnail_key", sa.String(255), nullable=False, unique=True),
        sa.Column("actual_mime", sa.String(80), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default="ready", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("bytes > 0", name="ck_photos_bytes"),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_photos_dimensions"),
        sa.CheckConstraint("status IN ('ready', 'deleting')", name="ck_photos_status"),
    )
    op.create_table(
        "animals",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("species", sa.String(24), nullable=False),
        sa.Column("name", sa.String(80)),
        sa.Column(
            "primary_photo_id",
            postgresql.UUID(),
            sa.ForeignKey("photos.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "city_id",
            postgresql.UUID(),
            sa.ForeignKey("cities.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column(
            "merged_into_id",
            postgresql.UUID(),
            sa.ForeignKey("animals.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("species IN ('cat', 'dog')", name="ck_animals_species"),
        sa.CheckConstraint(
            "status IN ('active', 'merged', 'hidden')", name="ck_animals_status"
        ),
        sa.CheckConstraint(
            "name IS NULL OR length(btrim(name)) > 0", name="ck_animals_name"
        ),
        sa.CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id <> id",
            name="ck_animals_not_self_merged",
        ),
        sa.CheckConstraint(
            "(status = 'merged') = (merged_into_id IS NOT NULL)",
            name="ck_animals_merge_status",
        ),
    )
    op.create_index("ix_animals_species_status", "animals", ["species", "status"])
    op.create_table(
        "encounters",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "animal_id",
            postgresql.UUID(),
            sa.ForeignKey("animals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "photo_id",
            postgresql.UUID(),
            sa.ForeignKey("photos.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "city_id",
            postgresql.UUID(),
            sa.ForeignKey("cities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("private_location", GeographyPoint()),
        sa.Column("public_location", GeographyPoint()),
        sa.Column("public_cell_key", sa.String(80)),
        sa.Column("geo_policy_version", sa.Integer()),
        sa.Column("comment", sa.String(500)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(public_location IS NULL) = (public_cell_key IS NULL) AND "
            "(public_location IS NULL) = (geo_policy_version IS NULL)",
            name="ck_encounters_public_geo_complete",
        ),
        sa.CheckConstraint(
            "private_location IS NULL OR public_location IS NOT NULL",
            name="ck_encounters_private_requires_public",
        ),
    )
    op.create_index(
        "ix_encounters_animal_observed",
        "encounters",
        ["animal_id", "observed_at", "id"],
    )
    op.create_index(
        "ix_encounters_user_observed", "encounters", ["user_id", "observed_at", "id"]
    )
    op.create_index("ix_encounters_created", "encounters", ["created_at", "id"])
    op.create_index(
        "ix_encounters_public_location_gist",
        "encounters",
        ["public_location"],
        postgresql_using="gist",
        postgresql_where=sa.text("deleted_at IS NULL AND public_location IS NOT NULL"),
    )
    op.create_table(
        "reactions",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "encounter_id",
            postgresql.UUID(),
            sa.ForeignKey("encounters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reaction_type", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reaction_type IN ('heart', 'laugh', 'love')", name="ck_reactions_type"
        ),
        sa.UniqueConstraint(
            "user_id",
            "encounter_id",
            "reaction_type",
            name="uq_reactions_actor_target_type",
        ),
    )
    op.create_index("ix_reactions_encounter", "reactions", ["encounter_id"])
    op.execute(
        sa.text(
            "INSERT INTO cities (id, slug, name, country_code, latitude, "
            "longitude, timezone, selection_radius_m) "
            "VALUES (:id, 'tbilisi', 'Tbilisi', 'GE', 41.7151, 44.8271, "
            "'Asia/Tbilisi', 25000) "
            "ON CONFLICT (slug) DO NOTHING"
        ).bindparams(sa.bindparam("id", UUID(TBILISI_ID), type_=postgresql.UUID()))
    )


def downgrade() -> None:
    op.drop_index("ix_reactions_encounter", table_name="reactions")
    op.drop_table("reactions")
    op.drop_index("ix_encounters_public_location_gist", table_name="encounters")
    op.drop_index("ix_encounters_created", table_name="encounters")
    op.drop_index("ix_encounters_user_observed", table_name="encounters")
    op.drop_index("ix_encounters_animal_observed", table_name="encounters")
    op.drop_table("encounters")
    op.drop_index("ix_animals_species_status", table_name="animals")
    op.drop_table("animals")
    op.drop_table("photos")
    op.drop_table("cities")
    op.drop_table("users")
    # PostGIS может использоваться другими объектами БД, поэтому extension не удаляем.
