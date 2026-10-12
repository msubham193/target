"""Phase 2 asset ingestion: blob, asset, raster_metadata, video_metadata

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "blob",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("storage_path", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_blob_sha256"),
    )

    op.create_table(
        "asset",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("blob_sha256", sa.Text, sa.ForeignKey("blob.sha256"), nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("modality", sa.Text, nullable=False),
        sa.Column("detected_format", sa.Text, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("duplicate_of_asset_id", UUID, sa.ForeignKey("asset.id")),
        sa.Column("origin", sa.Text, nullable=False, server_default="upload"),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("unknowns", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("quality_flags", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("acquisition_time", sa.DateTime(timezone=True)),
        sa.Column("uploaded_by", UUID, sa.ForeignKey("workspace_user.id"), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("modality IN ('ground_image', 'overhead_raster', 'video')", name="ck_asset_modality"),
        sa.CheckConstraint("status IN ('validated', 'validated_with_warnings')", name="ck_asset_status"),
    )
    op.create_index("ix_asset_project", "asset", ["project_id", "ingested_at", "id"])
    op.create_index("ix_asset_blob", "asset", ["blob_sha256"])
    op.create_index("ix_asset_quality_flags", "asset", ["quality_flags"], postgresql_using="gin")

    op.create_table(
        "raster_metadata",
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("driver", sa.Text, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("band_count", sa.Integer, nullable=False),
        sa.Column("dtypes", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("nodata", postgresql.JSONB, nullable=False),
        sa.Column("crs_status", sa.Text, nullable=False),
        sa.Column("crs_identifier", sa.Text),
        sa.Column("crs_name", sa.Text),
        sa.Column("crs_wkt", sa.Text),
        sa.Column("georeferenced", sa.Boolean, nullable=False),
        sa.Column("geotransform", postgresql.ARRAY(sa.Float)),
        sa.Column("pixel_size_x", sa.Float),
        sa.Column("pixel_size_y", sa.Float),
        sa.Column("pixel_units", sa.Text),
        sa.Column("bounds_native", postgresql.JSONB),
        sa.Column("footprint_4326", Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)),
        sa.CheckConstraint("crs_status IN ('valid', 'missing', 'invalid')", name="ck_raster_crs_status"),
        # A footprint may exist only for a validly georeferenced raster: coordinates are never invented.
        sa.CheckConstraint(
            "(georeferenced AND crs_status = 'valid') OR (NOT georeferenced AND footprint_4326 IS NULL "
            "AND pixel_units IS NULL)",
            name="ck_raster_no_invented_georeferencing",
        ),
    )
    op.create_index("ix_raster_footprint", "raster_metadata", ["footprint_4326"], postgresql_using="gist")

    op.create_table(
        "video_metadata",
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), primary_key=True),
        sa.Column("container", sa.Text, nullable=False),
        sa.Column("video_codec", sa.Text),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.BigInteger),
        sa.Column("avg_frame_rate", sa.Text),
        sa.Column("frame_rate", sa.Float),
        sa.Column("time_base", sa.Text),
        sa.Column("frame_count", sa.BigInteger),
        sa.Column("creation_time", sa.DateTime(timezone=True)),
        sa.Column("has_audio", sa.Boolean, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("video_metadata")
    op.drop_table("raster_metadata")
    op.drop_table("asset")
    op.drop_table("blob")
