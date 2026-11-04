"""Phase 3b: non-AI raster tools — statistics, display copies, pixel comparisons

Adds the job kinds for band statistics, verified display copies (overviews) and pixel comparisons, and
`asset_lineage`, which links a derived raster (a pixel comparison) to the rasters it was computed from.
Lineage rows are append-only.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

OLD_KINDS = "kind IN ('preview', 'analyze', 'selftest')"
NEW_KINDS = "kind IN ('preview', 'analyze', 'selftest', 'statistics', 'display_copy', 'compare')"


def upgrade() -> None:
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint("ck_job_kind", "job", NEW_KINDS)
    op.create_check_constraint("ck_asset_origin", "asset", "origin IN ('upload', 'derived')")

    op.create_table(
        "asset_lineage",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("child_asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("parent_asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("relation", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("tool_version", sa.Text, nullable=False),
        sa.Column("parameters", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("parent_checksum", sa.Text, nullable=False),
        sa.Column("parent_georef_version", sa.Integer, nullable=False),
        sa.Column("created_by_job_id", UUID, sa.ForeignKey("job.id")),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("relation IN ('pixel_difference_of')", name="ck_asset_lineage_relation"),
        sa.CheckConstraint("role IN ('a', 'b')", name="ck_asset_lineage_role"),
        sa.CheckConstraint("child_asset_id <> parent_asset_id", name="ck_asset_lineage_not_self"),
        sa.UniqueConstraint("child_asset_id", "role", name="uq_asset_lineage_child_role"),
    )
    op.create_index("ix_asset_lineage_parent", "asset_lineage", ["parent_asset_id"])
    op.execute(
        "CREATE TRIGGER tr_asset_lineage_append_only BEFORE UPDATE OR DELETE ON asset_lineage "
        "FOR EACH ROW EXECUTE FUNCTION append_only()"
    )


def downgrade() -> None:
    op.drop_table("asset_lineage")
    op.drop_constraint("ck_asset_origin", "asset", type_="check")
    op.execute(
        "DELETE FROM job_event WHERE job_id IN (SELECT id FROM job WHERE kind IN ('statistics', 'display_copy', 'compare'))"
    )
    op.execute("DELETE FROM artifact WHERE kind IN ('statistics', 'display_copy')")
    op.execute("DELETE FROM job WHERE kind IN ('statistics', 'display_copy', 'compare')")
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint("ck_job_kind", "job", OLD_KINDS)
