"""Fixed server-side ceilings and the append-only local usage log (operating model A).

Every outbound Earth Engine request is first *reserved* here. The reservation is atomic within the process (one lock
covers check-and-reserve) and is written to the log before anything is sent. Combined with the single-instance guard,
which keeps this the only process, that gives the guarantee model A needs without a database: the ceilings cannot be
passed by concurrent callers.

The log is an append-only JSON-lines file in the control folder, outside PostgreSQL and the repository. Each line
carries the hash of the one before it, so an edit or a lost line is detected. It is replayed at start-up to restore the
day's totals; a reservation that was never finished (the process died) counts as used. If the log cannot be read, is
damaged, or cannot be written, Earth Engine refuses to run (fail closed). It holds the operation name, unit counts, an
actor label and outcome codes; never a credential, an area of interest, coordinates, an image or a signed URL.

It is an evaluation aid, not the audit record of record, and it is not a substitute for the persistent ledger of
operating model B (Migration L, separately approved).
"""

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from geopoc.config import Settings
from geopoc.earth_engine.control import ControlPaths
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.satellite.gate import redact

GENESIS = "0" * 64


@dataclass(frozen=True)
class Ceilings:
    """Requests, not money: each unit is one outbound Earth Engine request (a credential refresh counts as one)."""

    per_day: int
    per_operation: int
    concurrent: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "Ceilings":
        mine = cls(
            settings.earth_engine_max_requests_per_day,
            settings.earth_engine_max_requests_per_operation,
            settings.earth_engine_max_concurrent,
        )
        for name, value, ceiling in (
            ("GEOPOC_EARTH_ENGINE_MAX_REQUESTS_PER_DAY", mine.per_day, ABSOLUTE_MAX.per_day),
            ("GEOPOC_EARTH_ENGINE_MAX_REQUESTS_PER_OPERATION", mine.per_operation, ABSOLUTE_MAX.per_operation),
            ("GEOPOC_EARTH_ENGINE_MAX_CONCURRENT", mine.concurrent, ABSOLUTE_MAX.concurrent),
        ):
            if not 1 <= value <= ceiling:
                raise EarthEngineRefused(
                    "EE_NOT_CONFIGURED",
                    f"{name} must be between 1 and {ceiling} (the hard bound for the controlled evaluation); got "
                    f"{value}. "
                    "Settings may lower a ceiling but never raise it past the bound.",
                )
        return mine


#: Bounds that settings cannot raise. Raising them is a code change, which is reviewed.
ABSOLUTE_MAX = Ceilings(per_day=100, per_operation=5, concurrent=1)


@dataclass
class Reservation:
    id: str
    operation: str
    units: int
    actor: str
    day: date
    finished: bool = False


def _line_hash(previous: str, body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((previous + canonical).encode("utf-8")).hexdigest()


def _clean(value: Any, limit: int = 120) -> str:
    return redact(str(value))[:limit]


class UsageLog:
    def __init__(
        self,
        paths: ControlPaths,
        ceilings: Ceilings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.paths = paths
        self.ceilings = ceilings
        self._clock = clock
        self._lock = threading.RLock()
        self._previous = GENESIS
        self._seq = 0
        # reservation id -> (day, units) for every reservation that is not released; used to total a day
        self._used: dict[str, tuple[date, int]] = {}
        self._active: set[str] = set()  # reserved in this process and not yet finished
        self._replay()

    # --- reading ------------------------------------------------------------------------------------------------------

    def _replay(self) -> None:
        path = self.paths.usage_log
        try:
            if not path.exists():
                return
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EarthEngineRefused(
                "EE_LOG_UNAVAILABLE", f"The usage log cannot be read ({type(exc).__name__}). Earth Engine stays off."
            ) from None
        previous = GENESIS
        for number, raw in enumerate(text.splitlines(), start=1):
            try:
                entry = json.loads(raw)
                claimed = entry.pop("hash")
                if entry["prev"] != previous or _line_hash(previous, entry) != claimed:
                    raise ValueError("chain broken")
                at = datetime.fromisoformat(entry["at"])
            except (ValueError, KeyError, TypeError):
                raise EarthEngineRefused(
                    "EE_LOG_UNAVAILABLE",
                    f"The usage log is damaged at line {number} (edited, truncated or out of order). Earth Engine "
                    f"stays "
                    "off until the owner has inspected it (`python -m geopoc.earth_engine verify-log`).",
                ) from None
            previous = claimed
            self._seq = entry["seq"]
            event = entry["event"]
            if event == "reserve":
                self._used[entry["id"]] = (at.astimezone(UTC).date(), int(entry["units"]))
            elif event == "commit":
                actual = entry.get("actual_units")
                if isinstance(actual, int) and entry["id"] in self._used:
                    day, units = self._used[entry["id"]]
                    self._used[entry["id"]] = (day, max(units, actual))
            elif event == "release":
                self._used.pop(entry["id"], None)
        self._previous = previous

    # --- writing ------------------------------------------------------------------------------------------------------

    def _append(self, event: str, **fields: Any) -> None:
        """Write one line, flushed to disk, before the caller does anything that depends on it."""
        body: dict[str, Any] = {
            "seq": self._seq + 1,
            "at": self._clock().astimezone(UTC).isoformat(),
            "event": event,
            **fields,
            "prev": self._previous,
        }
        digest = _line_hash(self._previous, body)
        line = json.dumps({**body, "hash": digest}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        try:
            with open(self.paths.usage_log, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise EarthEngineRefused(
                "EE_LOG_UNAVAILABLE",
                f"The usage log cannot be written ({type(exc).__name__}). Earth Engine stays off (fail closed).",
            ) from None
        self._previous = digest
        self._seq += 1

    def _today(self) -> date:
        return self._clock().astimezone(UTC).date()

    def used_today(self) -> int:
        with self._lock:
            today = self._today()
            return sum(units for day, units in self._used.values() if day == today)

    # --- the reservation ----------------------------------------------------------------------------------------------

    def reserve(self, operation: str, units: int, actor: str, request_id: str | None = None) -> Reservation:
        """Atomically check every ceiling and record the reservation, or refuse. Nothing is sent before this returns."""
        with self._lock:
            problem = None
            if not 1 <= units <= self.ceilings.per_operation:
                problem = (
                    f"one operation may use at most {self.ceilings.per_operation} request(s); this one needs {units}"
                )
            elif len(self._active) >= self.ceilings.concurrent:
                problem = f"only {self.ceilings.concurrent} Earth Engine operation(s) may run at a time"
            elif self.used_today() + units > self.ceilings.per_day:
                problem = (
                    f"the daily ceiling of {self.ceilings.per_day} request(s) would be passed "
                    f"({self.used_today()} used today, {units} needed)"
                )
            if problem:
                self.note("refuse", operation=operation, units=units, actor=_clean(actor), code="EE_BUDGET_EXCEEDED")
                raise EarthEngineRefused("EE_BUDGET_EXCEEDED", f"Usage ceiling reached: {problem}. Nothing was sent.")
            reservation = Reservation(uuid.uuid4().hex, operation, units, _clean(actor, 80), self._today())
            self._append(
                "reserve",
                id=reservation.id,
                operation=operation,
                units=units,
                actor=reservation.actor,
                request_id=_clean(request_id or "", 64),
            )
            self._used[reservation.id] = (reservation.day, units)
            self._active.add(reservation.id)
            return reservation

    def commit(
        self,
        reservation: Reservation,
        outcome: str,
        *,
        actual_units: int | None = None,
        estimate: bool = True,
        detail: str = "",
    ) -> None:
        """Record that the operation ran (or was attempted). The reserved units stay counted."""
        with self._lock:
            if reservation.finished:
                return
            reservation.finished = True
            self._active.discard(reservation.id)
            try:
                self._append(
                    "commit",
                    id=reservation.id,
                    operation=reservation.operation,
                    outcome=_clean(outcome, 40),
                    actual_units=actual_units,
                    actual_is_estimate=estimate,
                    detail=_clean(detail, 160),
                )
            except EarthEngineRefused:
                pass  # the reservation is already on disk and stays counted; the next call will refuse

    def release(self, reservation: Reservation, reason: str) -> None:
        """Give units back: only for an operation that never sent anything."""
        with self._lock:
            if reservation.finished:
                return
            reservation.finished = True
            self._active.discard(reservation.id)
            self._append("release", id=reservation.id, operation=reservation.operation, reason=_clean(reason, 80))
            self._used.pop(reservation.id, None)

    def note(self, event: str, **fields: Any) -> None:
        """Best-effort record of something that is not a reservation (a refusal, a verification result)."""
        with self._lock:
            try:
                self._append(event, **{k: (_clean(v, 160) if isinstance(v, str) else v) for k, v in fields.items()})
            except EarthEngineRefused:
                pass  # we are already refusing or reporting; the caller does not depend on this line

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ceilings": {
                    "requests_per_day": self.ceilings.per_day,
                    "requests_per_operation": self.ceilings.per_operation,
                    "concurrent_operations": self.ceilings.concurrent,
                },
                "used_today": self.used_today(),
                "remaining_today": max(0, self.ceilings.per_day - self.used_today()),
                "running_now": len(self._active),
                "log_lines": self._seq,
            }


def verify_file(paths: ControlPaths) -> dict[str, Any]:
    """Read-only check of the log's chain, for `verify-log`. Never raises; reports what it found."""
    try:
        log = UsageLog(paths, Ceilings(1, 1, 1))
    except EarthEngineRefused as exc:
        return {"ok": False, "problem": str(exc)}
    return {"ok": True, "lines": log._seq, "used_today": log.used_today()}
