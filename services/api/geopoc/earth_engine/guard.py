"""The single-instance guard: an exclusive lock on a file in the control folder.

Model A allows one API process and at most one worker to do Earth Engine work on one host. The first process to need
Earth Engine takes the lock and holds it until it exits; a second process that tries is refused. The lock is released by
the operating system when the holder dies, so a crash cannot leave Earth Engine locked.

This protects one host. A second instance on another host could bypass it, which is one reason the model is limited to
named developers (section 16.3a, rule 5).
"""

import os
import sys
import threading
from pathlib import Path

from geopoc.earth_engine.errors import EarthEngineRefused

_REGISTRY: dict[str, "InstanceGuard"] = {}
_REGISTRY_LOCK = threading.Lock()


class InstanceGuard:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        """Idempotent within a process. Raises EE_INSTANCE_CONFLICT if another process holds the lock."""
        if self._handle is not None:
            return
        try:
            handle = open(self.path, "a+b")  # noqa: SIM115 - held for the life of the process
        except OSError as exc:
            raise EarthEngineRefused(
                "EE_CONTROL_UNAVAILABLE", f"The single-instance lock could not be opened ({type(exc).__name__})."
            ) from None
        try:
            _lock(handle)
        except OSError:
            handle.close()
            raise EarthEngineRefused(
                "EE_INSTANCE_CONFLICT",
                "Another process is already using Earth Engine on this host (operating model A allows one API process "
                "and at most one worker). Nothing was sent.",
            ) from None
        try:
            handle.seek(1)
            handle.truncate(1)
            handle.write(f"pid {os.getpid()}\n".encode())
            handle.flush()
        except OSError:
            pass  # the pid is only a hint for people
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()


def _lock(handle) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - the project runs on Windows
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def guard_for(path: Path) -> InstanceGuard:
    """The one guard object for this lock file in this process."""
    key = str(path.resolve())
    with _REGISTRY_LOCK:
        return _REGISTRY.setdefault(key, InstanceGuard(path))


def release_all() -> None:
    """For tests and orderly shutdown."""
    with _REGISTRY_LOCK:
        guards = list(_REGISTRY.values())
        _REGISTRY.clear()
    for guard in guards:
        guard.release()
