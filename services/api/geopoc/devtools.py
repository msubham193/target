"""Development helpers. `python -m geopoc.devtools reset-db` recreates a scratch database and migrates it."""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from geopoc.config import get_settings

API_DIR = Path(__file__).resolve().parents[1]


def reset_db() -> None:
    settings = get_settings()
    name = settings.db_name
    # Only scratch databases (geopoc_e2e, geopoc_test, ...) may be dropped; never the working database.
    if not name.startswith("geopoc_") or not name.replace("_", "").isalnum():
        raise SystemExit(f"Refusing to reset database {name!r}: only scratch databases geopoc_* are allowed.")

    admin = create_engine(settings.database_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    command.upgrade(cfg, "head")
    print(f"Database {name} recreated and migrated.")


if __name__ == "__main__":
    if sys.argv[1:] == ["reset-db"]:
        reset_db()
    else:
        raise SystemExit("usage: python -m geopoc.devtools reset-db")
