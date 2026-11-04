"""PT-02: migrations apply and roll back on an empty database."""

from alembic import command
from sqlalchemy import create_engine, inspect, text

from geopoc.config import get_settings
from tests.conftest import alembic_config, drop_database, recreate_database

SCRATCH = "geopoc_migration_test"
TABLES = {
    "workspace_user",
    "project",
    "audit_event",
    "crs_definition",
    "blob",
    "asset",
    "raster_metadata",
    "video_metadata",
    "job",
    "job_event",
    "worker_heartbeat",
    "artifact",
    "pipeline_run",
    "observation",
    "observation_revision",
    "conflict",
    "conflict_member",
    "link_group",
    "link_member",
    "georef_assertion",
    "asset_lineage",
}


def test_upgrade_downgrade_upgrade():
    recreate_database(SCRATCH, drop=True)
    url = get_settings().database_url(SCRATCH)
    cfg = alembic_config(url)
    engine = create_engine(url)
    try:
        command.upgrade(cfg, "head")
        assert TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.execute(text("SELECT postgis_lib_version()")).scalar_one()
            index_method = conn.execute(
                text(
                    "SELECT am.amname FROM pg_class c JOIN pg_am am ON am.oid = c.relam "
                    "WHERE c.relname = 'ix_project_aoi_geom'"
                )
            ).scalar_one()
            assert index_method == "gist"

        command.downgrade(cfg, "base")
        assert not (TABLES & set(inspect(engine).get_table_names()))

        command.upgrade(cfg, "head")
        assert TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        drop_database(SCRATCH)
