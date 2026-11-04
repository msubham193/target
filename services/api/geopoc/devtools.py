"""Development helpers. `python -m geopoc.devtools reset-db` recreates a scratch database and migrates it."""

import os
import shutil
import stat
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

    if settings.data_subdir and settings.data_root.exists():
        # Scratch data folder only: originals are read-only, so clear that bit before deleting.
        def make_writable(function, target, _exc):
            os.chmod(target, stat.S_IWRITE)
            function(target)

        shutil.rmtree(settings.data_root, onerror=make_writable)

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    command.upgrade(cfg, "head")
    print(f"Database {name} recreated and migrated.")


def fetch_model(key: str) -> None:
    """Download one model's weights from the URL in the manifest, once, and verify them.

    This is the only command in the project that downloads anything, and it sends nothing: it is a plain file
    download run by hand at installation time. The application itself never fetches models.
    """
    import hashlib
    import urllib.request

    from geopoc.registry.manifest import load_manifest

    name, _, version = key.partition("@")
    entry = load_manifest().get(name, version)
    if entry is None:
        raise SystemExit(f"{key} is not in the model manifest.")
    target = entry.weights_file()
    if target is None or not entry.weights_source_url:
        raise SystemExit(f"{key} has no weights path and source_url in the manifest.")
    if not entry.weights_source_url.startswith("https://"):
        raise SystemExit("Weights are only downloaded over https.")

    recorded = entry.weights_sha256 if entry.weights_sha256 and len(entry.weights_sha256) == 64 else None

    def checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    if target.is_file():
        actual = checksum(target)
        if recorded and actual != recorded:
            raise SystemExit(f"{target} exists but does not match the manifest checksum. Remove it and run again.")
        print(f"Already installed: {target}\nsize_bytes: {target.stat().st_size}\nsha256: {actual}")
        return

    head = urllib.request.Request(entry.weights_source_url, method="HEAD")
    with urllib.request.urlopen(head, timeout=60) as response:
        size = int(response.headers["Content-Length"])
    print(f"{key}: {size / 1024**2:.1f} MB from {entry.weights_source_url}")
    if entry.weights_size_bytes is not None and size != entry.weights_size_bytes:
        raise SystemExit(f"The server reports {size} bytes but the manifest says {entry.weights_size_bytes}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target.parent).free
    if free - size < get_settings().storage_reserve_bytes:
        raise SystemExit(f"Not enough free disk space ({free / 1024**3:.1f} GB free).")

    part = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    with urllib.request.urlopen(entry.weights_source_url, timeout=120) as response, open(part, "wb") as handle:
        for block in iter(lambda: response.read(1024 * 1024), b""):
            digest.update(block)
            handle.write(block)
    actual = digest.hexdigest()
    if part.stat().st_size != size or (recorded and actual != recorded):
        part.unlink()
        raise SystemExit("The download does not match the expected size or checksum and was discarded.")
    os.replace(part, target)
    print(f"Installed: {target}\nsize_bytes: {size}\nsha256: {actual}")
    if not recorded:
        print("Record this sha256 in models/manifest.yaml; the model stays unavailable until it is there.")


if __name__ == "__main__":
    if sys.argv[1:] == ["reset-db"]:
        reset_db()
    elif len(sys.argv) == 3 and sys.argv[1] == "fetch-model":
        fetch_model(sys.argv[2])
    else:
        raise SystemExit("usage: python -m geopoc.devtools reset-db | fetch-model <name>@<version>")
