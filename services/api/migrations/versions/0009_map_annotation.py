"""Map annotations: points, lines and polygons drawn by a person, with a note, append-only

An annotation is the person's own mark on the map. It belongs to a project and to nothing else: no column refers to a
photograph, an observation, a raster or a job, so a mark can never be mistaken for a finding or a photograph location.
Each change is a new row (version) and deleting is a row with status 'deleted'; nothing is edited or removed, so the
history is kept. The latest version of an annotation is its current state.

Revision numbering: this migration follows 0008. The audit-protection migration (Phase 0 item 5) is not written yet and
is independent of this one (it concerns audit_event only). Whichever is applied second must take the next number and
point `down_revision` at the other; tests/test_migration_chain.py fails when the chain is not one straight line.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

# The stored geometry is always a valid, two-dimensional point, line or simple polygon (no holes) in WGS 84 degrees,
# within the longitude/latitude range. A CHECK passes when its expression is NULL, so the column is also NOT NULL.
GEOMETRY_RULE = (
    "NOT ST_IsEmpty(geom) AND ST_IsValid(geom) AND ST_NDims(geom) = 2 AND "
    "((kind = 'point' AND GeometryType(geom) = 'POINT') OR "
    " (kind = 'line' AND GeometryType(geom) = 'LINESTRING') OR "
    " (kind = 'polygon' AND GeometryType(geom) = 'POLYGON' AND ST_NumInteriorRings(geom) = 0)) AND "
    "ST_XMin(geom) >= -180 AND ST_XMax(geom) <= 180 AND ST_YMin(geom) >= -90 AND ST_YMax(geom) <= 90"
)


def upgrade() -> None:
    op.create_table(
        "map_annotation",
        sa.Column("id", UUID, primary_key=True),
        # Stable across versions. The first version's `id` is not reused: this is its own identifier.
        sa.Column("annotation_id", UUID, nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("geom", Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=False),
        sa.Column("crs_id", sa.Text, nullable=False, server_default="EPSG:4326"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("reason", sa.Text),
        sa.Column("actor_id", UUID, sa.ForeignKey("workspace_user.id")),
        sa.Column("actor_label", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("request_id", sa.Text),
        sa.UniqueConstraint("annotation_id", "version_no", name="uq_map_annotation_version"),
        sa.CheckConstraint("kind IN ('point', 'line', 'polygon')", name="ck_map_annotation_kind"),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_map_annotation_status"),
        sa.CheckConstraint("action IN ('create', 'update', 'delete')", name="ck_map_annotation_action"),
        sa.CheckConstraint("crs_id = 'EPSG:4326'", name="ck_map_annotation_crs"),
        sa.CheckConstraint("char_length(note) <= 2000", name="ck_map_annotation_note"),
        sa.CheckConstraint("reason IS NULL OR char_length(reason) <= 1000", name="ck_map_annotation_reason"),
        sa.CheckConstraint(
            "(action = 'create' AND version_no = 1 AND status = 'active') OR "
            "(action = 'update' AND version_no > 1 AND status = 'active') OR "
            "(action = 'delete' AND version_no > 1 AND status = 'deleted')",
            name="ck_map_annotation_action_shape",
        ),
        sa.CheckConstraint(GEOMETRY_RULE, name="ck_map_annotation_geometry"),
    )
    op.create_index("ix_map_annotation_project", "map_annotation", ["project_id", "annotation_id", "version_no"])
    op.create_index("ix_map_annotation_geom", "map_annotation", ["geom"], postgresql_using="gist")
    op.execute(
        "CREATE TRIGGER tr_map_annotation_append_only BEFORE UPDATE OR DELETE ON map_annotation "
        "FOR EACH ROW EXECUTE FUNCTION append_only()"
    )


def downgrade() -> None:
    # Annotations are authored by a person and cannot be recreated; refuse rather than lose them.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM map_annotation) THEN
                RAISE EXCEPTION 'map annotations exist; downgrade refused';
            END IF;
        END $$;
        """
    )
    op.drop_table("map_annotation")
