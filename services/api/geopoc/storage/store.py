"""Content-addressed storage for original assets.

Originals are written once under DATA_ROOT/originals/<aa>/<bb>/<sha256>, marked read-only and never modified.
Uploads are streamed to DATA_ROOT/tmp/uploads on the same volume, so the final move is a rename.
"""

import os
import shutil
import stat
import time
import uuid
from pathlib import Path

from geopoc.config import get_settings

ORPHAN_AGE_SECONDS = 24 * 3600


class StorageError(Exception):
    pass


def data_root() -> Path:
    return get_settings().data_root


def uploads_dir() -> Path:
    """The folder for uploads in progress. Created on demand, so it survives being removed while running."""
    folder = data_root() / "tmp" / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def init_storage() -> None:
    (data_root() / "originals").mkdir(parents=True, exist_ok=True)
    uploads_dir().mkdir(parents=True, exist_ok=True)


def free_bytes() -> int:
    return shutil.disk_usage(data_root()).free


def new_temp_path() -> Path:
    return uploads_dir() / f"{uuid.uuid4().hex}.upload"


def relative_path(sha256: str) -> str:
    """Path relative to DATA_ROOT, as stored in the database."""
    return f"originals/{sha256[:2]}/{sha256[2:4]}/{sha256}"


def absolute_path(relative: str) -> Path:
    return data_root() / relative


def commit_original(temp_path: Path, sha256: str) -> str:
    """Move a fully received upload into place and make it read-only. Returns the relative path."""
    relative = relative_path(sha256)
    target = absolute_path(relative)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Identical content is already stored; the existing original is never replaced.
            temp_path.unlink(missing_ok=True)
        else:
            os.replace(temp_path, target)
            os.chmod(target, stat.S_IREAD)
    except OSError as exc:
        raise StorageError(f"Could not store the original file: {exc.strerror or exc}") from exc
    return relative


def remove_original(sha256: str) -> None:
    """Undo commit_original when the database transaction that referenced it failed."""
    target = absolute_path(relative_path(sha256))
    try:
        if target.exists():
            os.chmod(target, stat.S_IWRITE)
            target.unlink()
    except OSError:
        pass  # an unreferenced file is harmless; never mask the original error


def discard_temp(temp_path: Path) -> None:
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        pass


def remove_orphaned_uploads() -> int:
    """Delete interrupted uploads older than a day. Called at start-up."""
    removed = 0
    cutoff = time.time() - ORPHAN_AGE_SECONDS
    for path in uploads_dir().glob("*.upload"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
