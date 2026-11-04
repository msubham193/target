"""Demo phase 1: photo locations (EXIF candidates and person-recorded positions), append-only

A ground photograph has no georeferencing of its own. Two kinds of position can be attached to one, and each is a new
row, never an edit: an unverified candidate read from the photo's EXIF GPS block, and a confirmed position recorded by
a person (confirming the candidate, typing coordinates, or choosing a point on the map). The current state of a photo is
its latest row. A photo with no rows has no location. Nothing here changes the stored original, the asset row or any
observation.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "photo_location",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        # candidate: read from the file, not confirmed. confirmed: a person stands behind it. cleared: no location.
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("crs_id", sa.Text, nullable=False, server_default="EPSG:4326"),
        # For a confirmed EXIF position: the candidate row it confirms.
        sa.Column("candidate_version_no", sa.Integer),
        sa.Column("reason", sa.Text),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_checksum", sa.Text, nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("workspace_user.id")),
        sa.Column("actor_label", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("request_id", sa.Text),
        sa.UniqueConstraint("asset_id", "version_no", name="uq_photo_location_version"),
        sa.CheckConstraint("status IN ('candidate', 'confirmed', 'cleared')", name="ck_photo_location_status"),
        sa.CheckConstraint(
            "source IN ('exif', 'manual_entry', 'map_selection', 'none')", name="ck_photo_location_source"
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_photo_location_version"),
        sa.CheckConstraint("crs_id = 'EPSG:4326'", name="ck_photo_location_crs"),
        # Coordinates are stored only for candidates and confirmed positions, and only when they are real numbers in
        # range. A CHECK passes when its expression is NULL, so presence is tested explicitly. (PostgreSQL orders NaN
        # above every number, so NaN fails the range tests.)
        sa.CheckConstraint(
            "(status = 'cleared' AND latitude IS NULL AND longitude IS NULL AND source = 'none') OR "
            "(status <> 'cleared' AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 AND source <> 'none')",
            name="ck_photo_location_coordinates",
        ),
        # A candidate comes only from the file. A manual or map position is only ever confirmed.
        sa.CheckConstraint(
            "(status = 'candidate' AND source = 'exif') OR status <> 'candidate'", name="ck_photo_location_candidate"
        ),
        sa.CheckConstraint(
            "(source IN ('manual_entry', 'map_selection') AND status = 'confirmed') OR "
            "source NOT IN ('manual_entry', 'map_selection')",
            name="ck_photo_location_manual",
        ),
    )
    op.create_index("ix_photo_location_asset", "photo_location", ["asset_id", "version_no"])
    op.create_index("ix_photo_location_project", "photo_location", ["project_id", "asset_id"])
    op.execute(
        "CREATE TRIGGER tr_photo_location_append_only BEFORE UPDATE OR DELETE ON photo_location "
        "FOR EACH ROW EXECUTE FUNCTION append_only()"
    )


def downgrade() -> None:
    # Positions confirmed by a person are history that cannot be recreated; refuse rather than lose them.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM photo_location WHERE status = 'confirmed') THEN
                RAISE EXCEPTION 'confirmed photo locations exist; downgrade refused';
            END IF;
        END $$;
        """
    )
    op.drop_table("photo_location")
