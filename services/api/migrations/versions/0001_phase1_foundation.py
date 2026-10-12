"""Phase 1 foundation: workspace_user, project, audit_event, crs_definition

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "workspace_user",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "project",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("tags", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("aoi_geom", Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)),
        sa.Column("aoi_source", postgresql.JSONB),
        sa.Column("source_preference", sa.Text, nullable=False, server_default="local_only"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspace_user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("source_preference IN ('local_only', 'allow_online')", name="ck_project_source_preference"),
    )
    op.create_index("ix_project_created", "project", ["created_at", "id"])
    op.create_index("ix_project_aoi_geom", "project", ["aoi_geom"], postgresql_using="gist")
    op.create_index("ix_project_tags", "project", ["tags"], postgresql_using="gin")

    op.create_table(
        "audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspace_user.id")),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("request_id", sa.Text),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_entity", "audit_event", ["entity_type", "entity_id", "at"])

    op.create_table(
        "crs_definition",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identifier", sa.Text, nullable=False, unique=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("wkt2", sa.Text),
        sa.Column("units", sa.Text),
        sa.Column("axis_order", sa.Text),
        sa.Column("area_of_use", sa.Text),
        sa.Column("source_reference", sa.Text),
        sa.Column("approved_by", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="unclaimed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('authority', 'custom')", name="ck_crs_definition_kind"),
        sa.CheckConstraint("status IN ('active', 'unclaimed')", name="ck_crs_definition_status"),
    )


def downgrade() -> None:
    op.drop_table("crs_definition")
    op.drop_table("audit_event")
    op.drop_table("project")
    op.drop_table("workspace_user")
    # The postgis extension is left installed; other objects in the database may depend on it.
