"""Phase 3b follow-up: sidecar metadata (.tfw/.wld world files, .prj, GDAL .aux.xml) for review and confirmation

A sidecar is stored unmodified, like an original, and linked to one raster. It is evidence for a person to review,
never applied on its own: an accepted change becomes an ordinary `georef_assertion` version whose method says it came
from sidecar files. Sidecar rows are append-only; a corrected sidecar is a new upload.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

OLD_METHODS = (
    "method IN ('registry_crs_selected', 'geotransform_entered', 'world_file_entered', 'file_metadata_restored')"
)
NEW_METHODS = (
    "method IN ('registry_crs_selected', 'geotransform_entered', 'world_file_entered', 'file_metadata_restored', "
    "'sidecar_crs', 'sidecar_geotransform')"
)
OLD_SOURCE = "geotransform_source IS NOT NULL OR method NOT IN ('geotransform_entered', 'world_file_entered')"
NEW_SOURCE = (
    "geotransform_source IS NOT NULL OR method NOT IN ('geotransform_entered', 'world_file_entered', "
    "'sidecar_geotransform')"
)


def upgrade() -> None:
    op.create_table(
        "sidecar",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("blob_sha256", sa.Text, sa.ForeignKey("blob.sha256"), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("pairing", sa.Text, nullable=False),
        sa.Column("upload_status", sa.Text, nullable=False),
        sa.Column("parser_version", sa.Text, nullable=False),
        sa.Column("uploaded_by", UUID, sa.ForeignKey("workspace_user.id"), nullable=False),
        sa.Column("uploaded_at", TS, nullable=False),
        sa.Column("request_id", sa.Text),
        sa.CheckConstraint("kind IN ('world_file', 'prj', 'aux_xml')", name="ck_sidecar_kind"),
        sa.CheckConstraint("pairing IN ('filename_match', 'explicit')", name="ck_sidecar_pairing"),
        sa.CheckConstraint("upload_status IN ('parsed', 'malformed', 'unsupported')", name="ck_sidecar_status"),
        sa.CheckConstraint("size_bytes > 0", name="ck_sidecar_size"),
    )
    op.create_index("ix_sidecar_asset", "sidecar", ["asset_id", "kind", "uploaded_at"])
    op.execute(
        "CREATE TRIGGER tr_sidecar_append_only BEFORE UPDATE OR DELETE ON sidecar "
        "FOR EACH ROW EXECUTE FUNCTION append_only()"
    )
    op.drop_constraint("ck_georef_assertion_method", "georef_assertion", type_="check")
    op.create_check_constraint("ck_georef_assertion_method", "georef_assertion", NEW_METHODS)
    op.drop_constraint("ck_georef_assertion_source", "georef_assertion", type_="check")
    op.create_check_constraint("ck_georef_assertion_source", "georef_assertion", NEW_SOURCE)


def downgrade() -> None:
    # Versions recorded from sidecars cannot be expressed without them; refuse rather than lose history.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM georef_assertion WHERE method IN ('sidecar_crs', 'sidecar_geotransform')) THEN
                RAISE EXCEPTION 'georeferencing versions recorded from sidecar files exist; downgrade refused';
            END IF;
        END $$;
        """
    )
    op.drop_constraint("ck_georef_assertion_source", "georef_assertion", type_="check")
    op.create_check_constraint("ck_georef_assertion_source", "georef_assertion", OLD_SOURCE)
    op.drop_constraint("ck_georef_assertion_method", "georef_assertion", type_="check")
    op.create_check_constraint("ck_georef_assertion_method", "georef_assertion", OLD_METHODS)
    op.drop_table("sidecar")
