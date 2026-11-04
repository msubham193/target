"""The migration history is one straight line, and the annotation migration sits on it without clashing.

Another migration (audit-protection, Phase 0 item 5) is planned but not written. If two migrations ever claim the same
number or the same parent, these tests fail before anything is applied. They read the migration files only; the up/down
test uses a scratch database (geopoc_migration_ann), never the working one.
"""

import re

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from geopoc.config import get_settings
from tests.conftest import alembic_config, drop_database, recreate_database

SCRATCH = "geopoc_migration_ann"


def script():
    return ScriptDirectory.from_config(alembic_config(get_settings().database_url()))


def test_the_history_is_a_single_line_with_contiguous_numbers_matching_the_file_names():
    revisions = list(script().walk_revisions())  # newest first
    assert len(script().get_heads()) == 1, "two migrations descend from the same parent (a branch)"
    numbers = sorted(r.revision for r in revisions)
    assert numbers == [f"{n:04d}" for n in range(1, len(numbers) + 1)], "revision numbers must be 0001..N with no gaps"
    for revision in revisions:
        name = revision.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        assert re.fullmatch(rf"{revision.revision}_[a-z0-9_]+\.py", name), (
            f"{name} does not start with {revision.revision}"
        )
        expected_parent = None if revision.revision == "0001" else f"{int(revision.revision) - 1:04d}"
        assert revision.down_revision == expected_parent, f"{revision.revision} should follow {expected_parent}"


def test_the_annotation_migration_follows_the_photo_location_migration():
    annotation = script().get_revision("0009")
    assert annotation.down_revision == "0008"
    assert "map_annotation" in annotation.path or "annotation" in annotation.path


def test_annotation_migration_applies_on_top_of_0008_and_downgrades_only_when_empty():
    recreate_database(SCRATCH, drop=True)
    url = get_settings().database_url(SCRATCH)
    cfg = alembic_config(url)
    engine = create_engine(url)
    try:
        command.upgrade(cfg, "0008")
        assert "map_annotation" not in inspect(engine).get_table_names()  # the earlier schema is untouched
        before = {t: _columns(engine, t) for t in ("photo_location", "observation", "audit_event", "asset")}

        command.upgrade(cfg, "0009")
        tables = set(inspect(engine).get_table_names())
        assert "map_annotation" in tables
        # additive only: no existing table changed
        assert before == {t: _columns(engine, t) for t in before}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"
            index_method = conn.execute(
                text(
                    "SELECT am.amname FROM pg_class c JOIN pg_am am ON am.oid = c.relam "
                    "WHERE c.relname = 'ix_map_annotation_geom'"
                )
            ).scalar_one()
            assert index_method == "gist"
            trigger = conn.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = 'tr_map_annotation_append_only'")
            ).scalar_one()
            assert trigger == 1

        # with an annotation stored, downgrading is refused rather than losing it
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO workspace_user (id, display_name, created_at) VALUES (gen_random_uuid(), 'm', now())")
            )
            conn.execute(
                text(
                    "INSERT INTO project (id, name, source_preference, created_by, created_at, updated_at, tags) "
                    "SELECT gen_random_uuid(), 'p', 'local_only', id, now(), now(), '{}' FROM workspace_user LIMIT 1"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO map_annotation (id, annotation_id, project_id, version_no, action, status, kind, geom, "
                    "actor_label, created_at) SELECT gen_random_uuid(), gen_random_uuid(), p.id, 1, 'create', 'active', "
                    "'point', ST_GeomFromText('POINT(75 27)', 4326), 'm', now() FROM project p LIMIT 1"
                )
            )
        with pytest.raises(Exception, match="downgrade refused"):
            command.downgrade(cfg, "0008")
        assert "map_annotation" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        drop_database(SCRATCH)


def test_annotation_migration_downgrades_cleanly_when_no_annotation_exists():
    recreate_database(SCRATCH, drop=True)
    url = get_settings().database_url(SCRATCH)
    cfg = alembic_config(url)
    engine = create_engine(url)
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0008")
        assert "map_annotation" not in inspect(engine).get_table_names()
        command.upgrade(cfg, "head")
        assert "map_annotation" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        drop_database(SCRATCH)


def _columns(engine, table: str) -> list[tuple[str, str]]:
    return [(c["name"], str(c["type"])) for c in inspect(engine).get_columns(table)]
