"""The global kill switch: a flag file in the control folder, checked before every Earth Engine request and step.

It is held outside the database and the repository, needs no restart and no database, and fails closed: if the flag's
state cannot be determined (the folder is missing, unreadable, or the check raises), Earth Engine is treated as stopped.
Turning it back on is a separate, deliberate `resume`; nothing resumes automatically.
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from geopoc.earth_engine.control import ControlPaths
from geopoc.earth_engine.errors import EarthEngineRefused


@dataclass(frozen=True)
class KillState:
    engaged: bool
    reason: str


def state(paths: ControlPaths) -> KillState:
    """Never raises. Anything but a clearly absent flag in a readable folder counts as engaged."""
    try:
        if not paths.directory.is_dir():
            return KillState(True, "The control folder is missing or unreadable (fail closed).")
        try:
            os.stat(paths.kill_flag)
        except FileNotFoundError:
            return KillState(False, "")
        return KillState(True, "The kill switch is engaged.")
    except Exception:  # noqa: BLE001 - any failure to look means stopped
        return KillState(True, "The kill switch could not be read (fail closed).")


def assert_off(paths: ControlPaths) -> None:
    current = state(paths)
    if current.engaged:
        raise EarthEngineRefused("EE_KILL_SWITCH", f"{current.reason} Earth Engine is stopped; nothing was sent.")


def engage(paths: ControlPaths, by: str, reason: str) -> None:
    """Stop everything. Safe to repeat. The record in the flag is for people; only its existence matters."""
    paths.directory.mkdir(parents=True, exist_ok=True)
    note = {"at": datetime.now(UTC).isoformat(), "by": by[:80], "reason": reason[:300]}
    with open(paths.kill_flag, "w", encoding="utf-8") as handle:
        json.dump(note, handle)


def resume(paths: ControlPaths) -> bool:
    """Deliberately turn Earth Engine back on. Returns whether a flag was removed."""
    try:
        paths.kill_flag.unlink()
    except FileNotFoundError:
        return False
    return True
