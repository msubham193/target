"""The control folder: kill-switch flag, single-instance lock and the append-only usage log.

It lives outside the repository and outside PostgreSQL. It is created only by the owner's explicit
`python -m geopoc.earth_engine init`; if it is missing, unreadable or inside the repository, Earth Engine refuses to
run.
"""

from dataclasses import dataclass
from pathlib import Path

from geopoc.config import REPO_ROOT, Settings
from geopoc.earth_engine.errors import EarthEngineRefused

KILL_FLAG = "KILL"
LOCK_FILE = "instance.lock"
USAGE_LOG = "usage.log.jsonl"


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class ControlPaths:
    directory: Path

    @property
    def kill_flag(self) -> Path:
        return self.directory / KILL_FLAG

    @property
    def lock_file(self) -> Path:
        return self.directory / LOCK_FILE

    @property
    def usage_log(self) -> Path:
        return self.directory / USAGE_LOG

    @classmethod
    def from_settings(cls, settings: Settings) -> "ControlPaths":
        if settings.earth_engine_control_dir is None:
            raise EarthEngineRefused(
                "EE_NOT_CONFIGURED",
                "GEOPOC_EARTH_ENGINE_CONTROL_DIR is not set. Earth Engine needs a control folder outside the "
                "repository "
                "for its kill switch, single-instance lock and usage log; create it with "
                "`python -m geopoc.earth_engine init`. Nothing was sent.",
            )
        return cls(Path(settings.earth_engine_control_dir))

    def check_ready(self) -> None:
        """Refuse unless the folder exists, is a folder, and is not inside the repository. Creates nothing."""
        if _inside(self.directory, REPO_ROOT):
            raise EarthEngineRefused(
                "EE_CONTROL_UNAVAILABLE",
                "The Earth Engine control folder is inside the repository. It must be kept outside it.",
            )
        try:
            ok = self.directory.is_dir()
        except OSError:
            ok = False
        if not ok:
            raise EarthEngineRefused(
                "EE_CONTROL_UNAVAILABLE",
                "The Earth Engine control folder does not exist or cannot be read. Run "
                "`python -m geopoc.earth_engine init`. Earth Engine stays off (fail closed).",
            )

    def create(self) -> None:
        """Owner-run setup only (the `init` command). Refuses a folder inside the repository."""
        if _inside(self.directory, REPO_ROOT):
            raise EarthEngineRefused(
                "EE_CONTROL_UNAVAILABLE", "The control folder must be outside the repository; choose another path."
            )
        self.directory.mkdir(parents=True, exist_ok=True)
