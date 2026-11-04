"""The choke point every Earth Engine operation goes through (operating model A).

An operation is allowed only if, in this order: both global switches are on and a Cloud project is named; the caller is
a named developer; the control folder is ready; the kill switch is off; this is the only process using Earth Engine; the
usage log is healthy; and a reservation against the fixed ceilings succeeds. The kill switch is checked again before
each outbound request (`Operation.checkpoint`). Any failure to establish any of these is a refusal, never a pass.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from geopoc.config import Settings
from geopoc.earth_engine import guard, killswitch
from geopoc.earth_engine.control import ControlPaths
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.earth_engine.usage import Ceilings, Reservation, UsageLog
from geopoc.satellite.gate import EarthEngineDisabled, ensure_enabled


def developers(settings: Settings) -> frozenset[str]:
    return frozenset(name.strip() for name in settings.earth_engine_developers.split(",") if name.strip())


class Operation:
    """One reserved operation. `checkpoint()` before each outbound request; `release()` if nothing was sent."""

    def __init__(self, runtime: "Runtime", reservation: Reservation) -> None:
        self._runtime = runtime
        self.reservation = reservation
        self.name = reservation.operation
        self.units = reservation.units
        self.released = False
        self.outcome = "ok"
        self.detail = ""

    def checkpoint(self) -> None:
        """Re-check the kill switch (and that the log still works). Call immediately before every outbound request."""
        killswitch.assert_off(self._runtime.paths)

    def release(self, reason: str) -> None:
        self._runtime.usage.release(self.reservation, reason)
        self.released = True


@dataclass
class Runtime:
    settings: Settings
    paths: ControlPaths = field(init=False)
    ceilings: Ceilings = field(init=False)
    usage: UsageLog = field(init=False)
    # Filled by the verification workflow; read by the status endpoint.
    connection: Any = None
    last_verification: dict[str, Any] | None = None
    probes: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _ready: bool = False

    def __post_init__(self) -> None:
        self.paths = ControlPaths.from_settings(self.settings)
        self.ceilings = Ceilings.from_settings(self.settings)

    # --- the checks -------------------------------------------------------------------------------------------------

    def preflight(self, actor: str, *, require_developer: bool) -> None:
        try:
            ensure_enabled(self.settings)
        except EarthEngineDisabled as exc:
            raise EarthEngineRefused("EE_DISABLED", str(exc)) from None
        if not self.settings.earth_engine_project:
            raise EarthEngineRefused(
                "EE_NOT_CONFIGURED", "GEOPOC_EARTH_ENGINE_PROJECT is not set: name the one designated Cloud project."
            )
        if require_developer and actor not in developers(self.settings):
            raise EarthEngineRefused(
                "EE_DEVELOPER_ONLY",
                "Earth Engine is limited to named developers (GEOPOC_EARTH_ENGINE_DEVELOPERS) during the controlled "
                "evaluation. This profile is not listed.",
            )
        self.paths.check_ready()
        killswitch.assert_off(self.paths)
        guard.guard_for(self.paths.lock_file).acquire()
        with self._lock:
            if not self._ready:
                self.usage = UsageLog(self.paths, self.ceilings)
                self._ready = True

    @contextmanager
    def operation(
        self, name: str, units: int, actor: str, *, request_id: str | None = None, require_developer: bool = True
    ) -> Iterator[Operation]:
        try:
            self.preflight(actor, require_developer=require_developer)
            reservation = self.usage.reserve(name, units, actor, request_id)
        except EarthEngineRefused as exc:
            self._note_refusal(name, actor, exc.code)
            raise
        op = Operation(self, reservation)
        try:
            yield op
        except BaseException as exc:
            if not op.released:
                # The request may have been sent, so the reservation stays counted.
                self.usage.commit(reservation, "error", detail=getattr(exc, "code", type(exc).__name__))
            raise
        else:
            if not op.released:
                self.usage.commit(reservation, op.outcome, detail=op.detail)

    def _note_refusal(self, name: str, actor: str, code: str) -> None:
        # Only possible when the log is open; refusals before that (disabled, not configured) are the caller's answer.
        if self._ready:
            self.usage.note("refuse", operation=name, actor=actor, code=code)

    def snapshot(self) -> dict[str, Any]:
        """Local state only. Reads the log; never touches Google or the lock."""
        if not self._ready:
            try:
                self.paths.check_ready()
                self.usage = UsageLog(self.paths, self.ceilings)
                self._ready = True
            except EarthEngineRefused as exc:
                return {"log": {"ok": False, "problem": str(exc)}}
        return {"log": {"ok": True, **self.usage.snapshot()}}


# One runtime per configuration in this process.
_RUNTIMES: dict[tuple, Runtime] = {}
_RUNTIMES_LOCK = threading.Lock()


def _key(settings: Settings) -> tuple:
    return (
        str(settings.earth_engine_control_dir),
        settings.earth_engine_max_requests_per_day,
        settings.earth_engine_max_requests_per_operation,
        settings.earth_engine_max_concurrent,
    )


def get_runtime(settings: Settings) -> Runtime:
    """Raises EE_NOT_CONFIGURED when the control folder or a ceiling is not valid."""
    key = _key(settings)
    with _RUNTIMES_LOCK:
        runtime = _RUNTIMES.get(key)
        if runtime is None:
            runtime = Runtime(settings)
            _RUNTIMES[key] = runtime
        else:
            runtime.settings = settings  # switches and the developer list are read fresh; the log and lock are kept
        return runtime


def reset_runtimes() -> None:
    """Tests and orderly shutdown: forget the runtimes and release the single-instance lock."""
    with _RUNTIMES_LOCK:
        _RUNTIMES.clear()
    guard.release_all()
