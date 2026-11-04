"""Migration 0010 allows the job kind 'derived_index' and changes nothing else.

Everything here runs on a scratch database that the test creates and drops (geopoc_migration_dix). The working database
is never opened by these tests. The checks that matter for review: only `ck_job_kind` differs between the schema at 0009
and at 0010; the old kinds and the existing rows are unaffected; the downgrade refuses to discard derived_index jobs.
"""

import re

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from geopoc.config import database_environment, get_settings
from tests.conftest import alembic_config, drop_database, recreate_database

SCRATCH = "geopoc_migration_dix"
OLD = ["preview", "analyze", "selftest", "statistics", "display_copy", "compare"]
NEW = [*OLD, "derived_index"]


def insert_job(conn, kind: str) -> None:
    conn.execute(
        text(
            "INSERT INTO job (id, kind, status, cancel_requested, attempt, max_attempts, payload, has_partial_outputs, "
            "run_after, created_at) VALUES (gen_random_uuid(), :kind, 'queued', false, 0, 3, '{}', false, now(), now())"
        ),
        {"kind": kind},
    )


def schema_snapshot(conn) -> dict:
    """Every column, constraint, index and trigger, so two revisions can be compared object by object."""
    return {
        "columns": sorted(
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT table_name, column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_schema = 'public'"
                )
            )
        ),
        "constraints": sorted(
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE connamespace = 'public'::regnamespace"
                )
            )
        ),
        "indexes": sorted(
            tuple(r)
            for r in conn.execute(
                text("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            )
        ),
        "triggers": sorted(
            tuple(r)
            for r in conn.execute(text("SELECT tgrelid::regclass::text, tgname FROM pg_trigger WHERE NOT tgisinternal"))
        ),
        "tables": sorted(
            r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        ),
    }


def job_kind_definition(conn) -> str:
    return conn.execute(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_job_kind'")
    ).scalar_one()


def kinds_in(definition: str) -> list[str]:
    return sorted(re.findall(r"'([a-z_]+)'::text", definition))


@pytest.fixture
def scratch():
    recreate_database(SCRATCH, drop=True)
    url = get_settings().database_url(SCRATCH)
    engine = create_engine(url)
    try:
        yield alembic_config(url), engine
    finally:
        engine.dispose()
        drop_database(SCRATCH)


def test_these_tests_only_ever_use_a_scratch_database():
    assert database_environment(SCRATCH) == "scratch"
    assert database_environment(get_settings().db_name) == "scratch"  # the whole suite runs on geopoc_test, not geopoc


def test_the_revision_follows_0009_and_changes_one_constraint_by_name():
    script = ScriptDirectory.from_config(alembic_config(get_settings().database_url()))
    revision = script.get_revision("0010")
    assert revision.down_revision == "0009" and script.get_heads() == ["0010"]
    source = open(revision.path, encoding="utf-8").read()
    assert source.count("ck_job_kind") >= 4  # dropped and recreated, in both directions
    for other in (
        "create_table",
        "drop_table",
        "add_column",
        "drop_column",
        "create_index",
        "ck_asset_origin",
        "alter_column",
    ):
        assert other not in source, other


def test_0009_rejects_the_new_kind_and_0010_accepts_it_without_touching_anything_else(scratch):
    cfg, engine = scratch
    command.upgrade(cfg, "0009")
    with engine.begin() as conn:
        for kind in OLD:
            insert_job(conn, kind)  # rows that exist before the migration
        before = schema_snapshot(conn)
        before_definition = job_kind_definition(conn)
        counts_before = dict(conn.execute(text("SELECT kind, count(*) FROM job GROUP BY kind")).all())
    assert kinds_in(before_definition) == sorted(OLD)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        insert_job(conn, "derived_index")

    command.upgrade(cfg, "0010")
    with engine.begin() as conn:
        after = schema_snapshot(conn)
        after_definition = job_kind_definition(conn)
        counts_after = dict(conn.execute(text("SELECT kind, count(*) FROM job GROUP BY kind")).all())
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0010"
    assert kinds_in(after_definition) == sorted(NEW)
    assert counts_before == counts_after  # no row was changed, added or lost

    # Only the one constraint differs: same columns, indexes, triggers, tables and every other constraint.
    for part in ("columns", "indexes", "triggers", "tables"):
        assert before[part] == after[part], part
    changed = [row for row in before["constraints"] if row not in after["constraints"]]
    added = [row for row in after["constraints"] if row not in before["constraints"]]
    assert [r[1] for r in changed] == ["ck_job_kind"] and [r[1] for r in added] == ["ck_job_kind"]
    assert len(before["constraints"]) == len(after["constraints"])

    with engine.begin() as conn:
        insert_job(conn, "derived_index")  # now allowed
        for kind in OLD:
            insert_job(conn, kind)  # the old kinds still are
    with pytest.raises(IntegrityError), engine.begin() as conn:
        insert_job(conn, "not_a_kind")  # and the constraint still rejects what it should


def test_the_downgrade_refuses_to_discard_derived_index_jobs_and_otherwise_restores_0009(scratch):
    cfg, engine = scratch
    command.upgrade(cfg, "0010")
    with engine.begin() as conn:
        snapshot_0010 = schema_snapshot(conn)
        insert_job(conn, "derived_index")
        insert_job(conn, "preview")
    with pytest.raises((ProgrammingError, Exception), match="downgrade refused"):
        command.downgrade(cfg, "0009")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0010"
        assert kinds_in(job_kind_definition(conn)) == sorted(NEW)  # nothing was half-done
        assert conn.execute(text("SELECT count(*) FROM job")).scalar_one() == 2
        conn.execute(text("DELETE FROM job WHERE kind = 'derived_index'"))

    command.downgrade(cfg, "0009")
    with engine.begin() as conn:
        assert kinds_in(job_kind_definition(conn)) == sorted(OLD)
        assert conn.execute(text("SELECT count(*) FROM job")).scalar_one() == 1  # the preview job survived
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"
        restored = schema_snapshot(conn)
    assert [r for r in snapshot_0010["columns"]] == restored["columns"]
    command.upgrade(cfg, "head")  # and it can go forward again
    with engine.begin() as conn:
        assert kinds_in(job_kind_definition(conn)) == sorted(NEW)
