"""Phase 4 follow-up: audited georeferencing recorded by a person for rasters whose own metadata is missing or invalid

`raster_metadata` keeps what the file declares, exactly as ingested; a trigger now refuses changes to those columns.
Each change a person records is a new, append-only `georef_assertion` version with its validation results. The
raster's current version is pointed to by `raster_metadata.georef_version` / `current_assertion_id`, which are the
only columns that may change, and which also serve as the optimistic-concurrency counter.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

OPERATIONS = ("assign_crs", "set_geotransform", "revert_to_file")
METHODS = ("registry_crs_selected", "geotransform_entered", "world_file_entered", "file_metadata_restored")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "georef_assertion",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("operation", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("crs_id", sa.Text),
        sa.Column("crs_name", sa.Text),
        sa.Column("crs_wkt", sa.Text),
        sa.Column("geotransform", postgresql.ARRAY(sa.Float)),
        sa.Column("geotransform_source", sa.Text),
        sa.Column("world_file", sa.Text),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("previous", postgresql.JSONB, nullable=False),
        sa.Column("result", postgresql.JSONB, nullable=False),
        sa.Column("validation", postgresql.JSONB, nullable=False),
        sa.Column("georeferenced", sa.Boolean, nullable=False),
        sa.Column("footprint_4326", Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)),
        sa.Column("source_checksum", sa.Text, nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("workspace_user.id"), nullable=False),
        sa.Column("actor_label", sa.Text, nullable=False),
        sa.Column("request_id", sa.Text),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("asset_id", "version_no", name="uq_georef_assertion_version"),
        sa.CheckConstraint(_in("operation", OPERATIONS), name="ck_georef_assertion_operation"),
        sa.CheckConstraint(_in("method", METHODS), name="ck_georef_assertion_method"),
        sa.CheckConstraint("version_no >= 1", name="ck_georef_assertion_version"),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_georef_assertion_reason"),
        # Entered coordinates always say where they came from.
        sa.CheckConstraint(
            "geotransform_source IS NOT NULL OR method NOT IN ('geotransform_entered', 'world_file_entered')",
            name="ck_georef_assertion_source",
        ),
        sa.CheckConstraint(
            "geotransform IS NULL OR array_length(geotransform, 1) = 6", name="ck_georef_assertion_gt_length"
        ),
        # A placement exists only with a footprint, and vice versa.
        sa.CheckConstraint("georeferenced = (footprint_4326 IS NOT NULL)", name="ck_georef_assertion_footprint"),
    )
    op.create_index("ix_georef_assertion_asset", "georef_assertion", ["asset_id", "version_no"])
    op.execute(
        "CREATE TRIGGER tr_georef_assertion_append_only BEFORE UPDATE OR DELETE ON georef_assertion "
        "FOR EACH ROW EXECUTE FUNCTION append_only()"
    )

    op.add_column("raster_metadata", sa.Column("georef_version", sa.Integer, nullable=False, server_default="0"))
    op.add_column("raster_metadata", sa.Column("current_assertion_id", UUID, sa.ForeignKey("georef_assertion.id")))
    # What the file declares is a record of the file: only the pointer to the current version may change.
    op.execute(
        """
        CREATE FUNCTION raster_file_metadata_is_immutable() RETURNS trigger AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'georef_version' - 'current_assertion_id')
               IS DISTINCT FROM (to_jsonb(OLD) - 'georef_version' - 'current_assertion_id') THEN
                RAISE EXCEPTION 'the metadata read from the raster file cannot be changed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER tr_raster_file_metadata_is_immutable BEFORE UPDATE ON raster_metadata "
        "FOR EACH ROW EXECUTE FUNCTION raster_file_metadata_is_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER tr_raster_file_metadata_is_immutable ON raster_metadata")
    op.execute("DROP FUNCTION raster_file_metadata_is_immutable()")
    op.drop_column("raster_metadata", "current_assertion_id")
    op.drop_column("raster_metadata", "georef_version")
    op.drop_table("georef_assertion")
