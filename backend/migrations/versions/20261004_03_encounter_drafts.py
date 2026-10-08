"""Persistent encounter drafts and mandatory encounter photo."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision: str = "20261004_03"
down_revision: str | None = "20261004_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class GeographyPoint(UserDefinedType[object]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "geography(Point,4326)"


def upgrade() -> None:
    # Тот же lock, который требуется ALTER: старые writers не вставят NULL
    # между проверкой и NOT NULL. Не удаляем встречи и не угадываем их фото.
    op.execute("LOCK TABLE encounters IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM encounters WHERE photo_id IS NULL) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Cannot apply 20261004_03: encounters.photo_id contains NULL. '
                    || 'Restore verified photo associations manually before retrying; '
                    || 'no data was changed.';
            END IF;
        END $$
    """)
    op.alter_column(
        "encounters", "photo_id", existing_type=postgresql.UUID(), nullable=False
    )
    op.create_table(
        "encounter_drafts",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "photo_id",
            postgresql.UUID(),
            sa.ForeignKey("photos.id", ondelete="RESTRICT"),
        ),
        sa.Column("species", sa.String(24)),
        sa.Column(
            "location_set", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("private_location", GeographyPoint()),
        sa.Column("public_location", GeographyPoint()),
        sa.Column("public_cell_key", sa.String(80)),
        sa.Column("geo_policy_version", sa.Integer()),
        sa.Column(
            "city_id",
            postgresql.UUID(),
            sa.ForeignKey("cities.id", ondelete="RESTRICT"),
        ),
        sa.Column("selection", sa.String(16)),
        sa.Column(
            "animal_id",
            postgresql.UUID(),
            sa.ForeignKey("animals.id", ondelete="RESTRICT"),
        ),
        sa.Column("new_name", sa.String(80)),
        sa.Column("comment", sa.String(500)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "result_encounter_id",
            postgresql.UUID(),
            sa.ForeignKey("encounters.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('active', 'committed', 'cancelled')",
            name="ck_encounter_drafts_status",
        ),
        sa.CheckConstraint(
            "species IS NULL OR species IN ('cat', 'dog')",
            name="ck_encounter_drafts_species",
        ),
        sa.CheckConstraint(
            "selection IS NULL OR selection IN ('new', 'existing')",
            name="ck_encounter_drafts_selection",
        ),
        sa.CheckConstraint("version >= 0", name="ck_encounter_drafts_version"),
    )
    op.create_index(
        "uq_encounter_drafts_active_user",
        "encounter_drafts",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_encounter_drafts_active_user", table_name="encounter_drafts")
    op.drop_table("encounter_drafts")
    op.alter_column(
        "encounters", "photo_id", existing_type=postgresql.UUID(), nullable=True
    )
