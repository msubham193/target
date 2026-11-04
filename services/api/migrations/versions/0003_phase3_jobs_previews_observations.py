"""Phase 3: job, job_event, worker_heartbeat, artifact, pipeline_run, observation

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

JOB_STATUSES = (
    "draft", "queued", "validating", "processing", "needs_review",
    "completed", "completed_with_warnings", "failed", "cancelled",
)  # fmt: skip
REVIEW_STATUSES = ("proposed", "needs_review", "verified", "inconclusive", "disputed", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id")),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("stage", sa.Text),
        sa.Column("progress", sa.Float),
        sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB),
        sa.Column("error_code", sa.Text),
        sa.Column("error_message", sa.Text),
        sa.Column("has_partial_outputs", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("worker_id", sa.Text),
        sa.Column("heartbeat_at", TS),
        sa.Column("run_after", TS, nullable=False),
        sa.Column("retry_of_job_id", UUID, sa.ForeignKey("job.id")),
        sa.Column("created_by", UUID, sa.ForeignKey("workspace_user.id")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.CheckConstraint(_in("status", JOB_STATUSES), name="ck_job_status"),
        sa.CheckConstraint("kind IN ('preview', 'analyze', 'selftest')", name="ck_job_kind"),
        sa.CheckConstraint("progress IS NULL OR (progress >= 0 AND progress <= 1)", name="ck_job_progress"),
    )
    # The claim query: oldest runnable queued job first.
    op.create_index("ix_job_queue", "job", ["run_after", "created_at"], postgresql_where=sa.text("status = 'queued'"))
    op.create_index("ix_job_project", "job", ["project_id", "created_at", "id"])
    op.create_index("ix_job_asset", "job", ["asset_id", "kind", "created_at"])

    op.create_table(
        "job_event",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_id", UUID, sa.ForeignKey("job.id"), nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("level", sa.Text, nullable=False, server_default="info"),
        sa.Column("stage", sa.Text),
        sa.Column("message", sa.Text, nullable=False),
    )
    op.create_index("ix_job_event_job", "job_event", ["job_id", "id"])

    op.create_table(
        "worker_heartbeat",
        sa.Column("worker_id", sa.Text, primary_key=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("capabilities", postgresql.JSONB, nullable=False, server_default="{}"),
    )

    op.create_table(
        "artifact",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("variant", sa.Text, nullable=False),
        sa.Column("storage_path", sa.Text, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by_job_id", UUID, sa.ForeignKey("job.id")),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("asset_id", "kind", "variant", name="uq_artifact_asset_kind_variant"),
        # A derived file never lives where originals live.
        sa.CheckConstraint("storage_path NOT LIKE 'originals/%'", name="ck_artifact_not_an_original"),
    )

    op.create_table(
        "pipeline_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("job_id", UUID, sa.ForeignKey("job.id"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("pipeline", sa.Text, nullable=False),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("weights_sha256", sa.Text),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("device", sa.Text),
        sa.Column("library_versions", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("summary", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=False),
    )
    op.create_index("ix_pipeline_run_asset", "pipeline_run", ["asset_id", "started_at"])

    op.create_table(
        "observation",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("run_id", UUID, sa.ForeignKey("pipeline_run.id"), nullable=False),
        sa.Column("pipeline", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("review_status", sa.Text, nullable=False),
        sa.Column("score_raw", sa.Float),
        sa.Column("quality_flags", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("record", postgresql.JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(_in("review_status", REVIEW_STATUSES), name="ck_observation_review_status"),
        sa.CheckConstraint("pipeline IN ('ground_image', 'overhead_raster', 'video')", name="ck_observation_pipeline"),
        # Every stored observation cites at least one piece of evidence.
        sa.CheckConstraint("jsonb_array_length(record -> 'evidence') >= 1", name="ck_observation_has_evidence"),
    )
    op.create_index("ix_observation_project", "observation", ["project_id", "review_status"])
    op.create_index("ix_observation_asset", "observation", ["asset_id", "created_at"])
    op.create_index("ix_observation_run", "observation", ["run_id"])


def downgrade() -> None:
    op.drop_table("observation")
    op.drop_table("pipeline_run")
    op.drop_table("artifact")
    op.drop_table("worker_heartbeat")
    op.drop_table("job_event")
    op.drop_table("job")
