"""Allow the job kind 'derived_index' (spectral indices such as NDVI)

One constraint changes and nothing else: `ck_job_kind` on `job` gains the value 'derived_index'. No table, column,
index, trigger or other constraint is touched. Every existing row already satisfies the wider list, so the upgrade
cannot fail on data. Written in the style of 0006, which added the three raster-tool kinds the same way.

The downgrade refuses while any derived_index job exists, rather than deleting job history (0009 does the same for
annotations). Remove or keep those jobs deliberately first.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

OLD_KINDS = "kind IN ('preview', 'analyze', 'selftest', 'statistics', 'display_copy', 'compare')"
NEW_KINDS = "kind IN ('preview', 'analyze', 'selftest', 'statistics', 'display_copy', 'compare', 'derived_index')"


def upgrade() -> None:
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint("ck_job_kind", "job", NEW_KINDS)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM job WHERE kind = 'derived_index') THEN
                RAISE EXCEPTION 'derived_index jobs exist; downgrade refused';
            END IF;
        END $$;
        """
    )
    op.drop_constraint("ck_job_kind", "job", type_="check")
    op.create_check_constraint("ck_job_kind", "job", OLD_KINDS)
