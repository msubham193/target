"""Phase 4: observation review history, conflicts and possible-duplicate links

Adds the current (reviewed) category and a revision counter to `observation`, and the append-only
`observation_revision` log. The model's original output in `observation.record`, `category` and `score_raw` is
protected by a trigger so that a review can never overwrite it (D-010, R-018). Conflicts and possible-duplicate
links are deterministic records between observations; they never delete or merge their members (CON-001, R-016).

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

REVIEW_STATUSES = ("proposed", "needs_review", "verified", "inconclusive", "disputed", "rejected")
ACTIONS = ("confirm", "reject", "mark_inconclusive", "reopen", "relabel", "note", "system_dispute", "system_resolve")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("observation", sa.Column("current_category", sa.Text))
    op.add_column("observation", sa.Column("revision", sa.Integer, nullable=False, server_default="0"))
    op.execute("UPDATE observation SET current_category = category WHERE current_category IS NULL")
    op.alter_column("observation", "current_category", nullable=False)
    op.create_index("ix_observation_project_category", "observation", ["project_id", "current_category"])

    # The model's output is evidence: once stored it is never changed, only reviewed alongside.
    op.execute(
        """
        CREATE FUNCTION observation_original_is_immutable() RETURNS trigger AS $$
        BEGIN
            IF NEW.record IS DISTINCT FROM OLD.record
               OR NEW.category IS DISTINCT FROM OLD.category
               OR NEW.score_raw IS DISTINCT FROM OLD.score_raw
               OR NEW.asset_id IS DISTINCT FROM OLD.asset_id
               OR NEW.run_id IS DISTINCT FROM OLD.run_id THEN
                RAISE EXCEPTION 'the original model output of an observation cannot be changed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER tr_observation_original_is_immutable BEFORE UPDATE ON observation "
        "FOR EACH ROW EXECUTE FUNCTION observation_original_is_immutable()"
    )

    op.create_table(
        "observation_revision",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("observation_id", UUID, sa.ForeignKey("observation.id"), nullable=False),
        sa.Column("revision_no", sa.Integer, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("status_before", sa.Text, nullable=False),
        sa.Column("status_after", sa.Text, nullable=False),
        sa.Column("category_before", sa.Text, nullable=False),
        sa.Column("category_after", sa.Text, nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("actor_kind", sa.Text, nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("workspace_user.id")),
        sa.Column("actor_label", sa.Text, nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("request_id", sa.Text),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("observation_id", "revision_no", name="uq_observation_revision_no"),
        sa.CheckConstraint(_in("action", ACTIONS), name="ck_observation_revision_action"),
        sa.CheckConstraint(_in("status_before", REVIEW_STATUSES), name="ck_observation_revision_before"),
        sa.CheckConstraint(_in("status_after", REVIEW_STATUSES), name="ck_observation_revision_after"),
        sa.CheckConstraint("actor_kind IN ('reviewer', 'system')", name="ck_observation_revision_actor_kind"),
        # A reviewer's change is always attributed to a profile.
        sa.CheckConstraint("actor_kind = 'system' OR actor_id IS NOT NULL", name="ck_observation_revision_actor"),
        sa.CheckConstraint("revision_no >= 1", name="ck_observation_revision_no"),
    )
    op.execute(
        """
        CREATE FUNCTION append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER tr_observation_revision_append_only BEFORE UPDATE OR DELETE ON observation_revision "
        "FOR EACH ROW EXECUTE FUNCTION append_only()"
    )

    op.create_table(
        "conflict",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("attribute", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("rule", sa.Text, nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by_run_id", UUID, sa.ForeignKey("pipeline_run.id")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("resolved_at", TS),
        sa.Column("resolution_note", sa.Text),
        sa.CheckConstraint(
            "attribute IN ('category', 'measurement', 'location', 'timestamp')", name="ck_conflict_attr"
        ),
        sa.CheckConstraint("status IN ('open', 'resolved_documented')", name="ck_conflict_status"),
        sa.CheckConstraint("(status = 'open') = (resolved_at IS NULL)", name="ck_conflict_resolved_at_matches_status"),
    )
    op.create_index("ix_conflict_project", "conflict", ["project_id", "status"])
    op.create_table(
        "conflict_member",
        sa.Column("conflict_id", UUID, sa.ForeignKey("conflict.id"), primary_key=True),
        sa.Column("observation_id", UUID, sa.ForeignKey("observation.id"), primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_conflict_member_observation", "conflict_member", ["observation_id"])

    op.create_table(
        "link_group",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("created_by_run_id", UUID, sa.ForeignKey("pipeline_run.id")),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("kind IN ('possible_duplicate')", name="ck_link_group_kind"),
        sa.CheckConstraint("status IN ('candidate', 'confirmed', 'rejected')", name="ck_link_group_status"),
    )
    op.create_index("ix_link_group_project", "link_group", ["project_id", "kind"])
    op.create_table(
        "link_member",
        sa.Column("link_group_id", UUID, sa.ForeignKey("link_group.id"), primary_key=True),
        sa.Column("observation_id", UUID, sa.ForeignKey("observation.id"), primary_key=True),
    )
    op.create_index("ix_link_member_observation", "link_member", ["observation_id"])

    # Conflict and link members are kept, never removed (resolution changes the conflict's status instead).
    for table in ("conflict_member", "link_member"):
        op.execute(
            f"CREATE TRIGGER tr_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION append_only()"
        )


def downgrade() -> None:
    op.drop_table("link_member")
    op.drop_table("link_group")
    op.drop_table("conflict_member")
    op.drop_table("conflict")
    op.drop_table("observation_revision")
    op.execute("DROP FUNCTION append_only()")
    op.execute("DROP TRIGGER tr_observation_original_is_immutable ON observation")
    op.execute("DROP FUNCTION observation_original_is_immutable()")
    op.drop_index("ix_observation_project_category", table_name="observation")
    op.drop_column("observation", "revision")
    op.drop_column("observation", "current_category")
