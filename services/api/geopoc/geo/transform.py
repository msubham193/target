"""Which coordinate operations exist between two CRSs, how good each one is, and which may be used.

Moving coordinates between two datums is not one calculation. The local PROJ database can hold several registered
operations for the same pair (some accurate to a metre, some to hundreds of metres), operations that need a datum-shift
grid that is not installed, and a fallback "ballpark" operation that assumes the datums nearly coincide. PROJ picks one
by default and says nothing. This module makes the choice visible and refuses to make it silently:

  exact          no datum change: only a map projection is applied (stated accuracy 0). Used without confirmation.
  approximate    a registered operation with a documented accuracy. Used when it is the only one that applies; when
                 several apply, a person has to choose one (`selection_required`). When its accuracy is worse than
                 `ACK_ACCURACY_M`, or is not stated at all, it is also refused until a person has acknowledged that
                 specific operation (`TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED`).
  ballpark       PROJ's fallback, recognised by its structure (`has_ballpark_transformation`), never by wording alone.
                 Blocked unless explicitly accepted. An acknowledgement does not unlock it.
  unavailable    a registered operation exists but needs a grid that is not installed. Never used; the grid is named.

The accuracy is the figure published with the operation in the registry. It is a typical horizontal error of that
datum shift, not a guarantee, and says nothing about the accuracy of a map sheet or an image. Nothing is downloaded:
PROJ network access is switched off.
"""

import hashlib
import json
import logging
import re
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

import pyproj
from pyproj import CRS, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.enums import TransformDirection
from pyproj.exceptions import CRSError, ProjError
from pyproj.transformer import TransformerGroup

# Never fetch grids or definitions from the network.
pyproj.network.set_network_enabled(False)

log = logging.getLogger(__name__)

Klass = Literal["exact", "approximate", "ballpark", "unavailable"]
PlanStatus = Literal["exact", "approximate", "selection_required", "unavailable"]
USABLE: tuple[str, ...] = ("exact", "approximate")

# An approximate operation whose documented accuracy is worse than this (or not stated) is not used until a person has
# acknowledged that specific operation. Exactly this value needs no acknowledgement.
ACK_ACCURACY_M = 10.0
ACK_ABOVE_THRESHOLD = "accuracy_above_threshold"
ACK_NOT_STATED = "accuracy_not_stated"
ACK_REQUIRED_CODE = "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"

EXACT_ID = "datum-unchanged"
BALLPARK_ID = "ballpark"
_WRAPPED_AUTHORITY = re.compile(r"^[A-Z_]+\((.+)\)$")

KLASS_EXPLANATION: dict[str, str] = {
    "exact": "No datum change. Only a map projection is applied, so the result is exact to numerical precision.",
    "approximate": (
        "A registered datum shift with a documented accuracy. The real error is typically about that size, but it can "
        "be "
        "larger away from where the shift was measured."
    ),
    "ballpark": (
        "A rough fallback. PROJ assumes the two datums nearly coincide or applies a crude shift, and states no "
        "accuracy. "
        "The error can be tens to hundreds of metres or more. It is blocked unless you explicitly accept it."
    ),
    "unavailable": "A registered operation exists, but it needs a datum-shift grid that is not installed here.",
}

ACCURACY_NOTES: tuple[str, ...] = (
    "Accuracy is the horizontal figure published with the operation in the local PROJ/EPSG registry. It describes the "
    "datum shift only.",
    "It does not include errors in the map sheet or image itself, in a hand-entered georeferencing, or in the CRS you "
    "chose.",
    "No transformation grids are installed and nothing is downloaded, so grid-based operations are listed but "
    "never used.",
)


class TransformationError(Exception):
    """A transformation cannot be used as asked. `code` is stable; `plan` explains the options."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        plan: "Plan | None" = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail, self.plan = status, code, title, detail, plan
        self.extra = extra or {}  # machine-readable context, for example what has to be acknowledged


@dataclass(frozen=True)
class Grid:
    name: str
    available: bool


@dataclass(frozen=True)
class Operation:
    id: str
    name: str
    klass: Klass
    accuracy_m: float | None
    authority_code: str | None
    area_of_use: str | None
    grids: tuple[Grid, ...] = ()
    pipeline: str | None = field(default=None, repr=False)
    transformer: Transformer | None = field(default=None, repr=False, compare=False)
    # How a ballpark was recognised: "structural" (PROJ says so), "text" (only the wording says so), or "both".
    # A disagreement between the two is still treated as a ballpark (fail closed) and logged.
    ballpark_signal: str | None = None

    @property
    def usable(self) -> bool:
        return self.klass in USABLE

    @property
    def accuracy_stated(self) -> bool:
        return self.accuracy_m is not None

    @property
    def ack_reason(self) -> str | None:
        """Why a person must acknowledge this operation before it is used, or None when no acknowledgement is needed.

        Only an approximate operation can need one: exact is exact, a ballpark is blocked outright (an acknowledgement
        never unlocks it), and an unavailable one cannot run at all."""
        if self.klass != "approximate":
            return None
        if self.accuracy_m is None:
            return ACK_NOT_STATED
        return ACK_ABOVE_THRESHOLD if self.accuracy_m > ACK_ACCURACY_M else None

    @property
    def requires_acknowledgement(self) -> bool:
        return self.ack_reason is not None

    def summary(self) -> str:
        if self.klass == "exact":
            return "No datum change; exact."
        if self.klass == "approximate":
            if self.accuracy_m is None:
                return "Registered datum shift whose accuracy is not stated; needs acknowledgement before use."
            suffix = "; needs acknowledgement before use" if self.requires_acknowledgement else ""
            return f"Registered datum shift, documented accuracy about {_metres(self.accuracy_m)}{suffix}."
        if self.klass == "ballpark":
            return "Rough fallback with no documented accuracy; blocked unless accepted."
        missing = ", ".join(g.name for g in self.grids if not g.available) or "a grid"
        return f"Needs the grid {missing}, which is not installed."


@dataclass
class Plan:
    source: str
    target: str
    status: PlanStatus
    operations: list[Operation]
    recommended_id: str | None
    messages: list[str]

    @property
    def usable(self) -> list[Operation]:
        return [o for o in self.operations if o.usable]

    def find(self, operation_id: str) -> Operation | None:
        return next((o for o in self.operations if o.id == operation_id), None)

    def missing_grids(self) -> list[str]:
        names: list[str] = []
        for operation in self.operations:
            for grid in operation.grids:
                if not grid.available and grid.name not in names:
                    names.append(grid.name)
        return names


@dataclass(frozen=True)
class Acknowledgement:
    """A person's statement that they accept one specific operation despite its accuracy.

    It is bound to the operation id, the source CRS, the datum of the target and the accuracy value that was shown, so
    changing any of them (a different operation, a registry that now states another accuracy) makes it stale. Where it
    comes from is `scope`: "stored" was recorded with a georeferencing version or an AOI; "request" was supplied for
    this one call and is only accepted by the stateless calculator, never for stored data.
    """

    operation_id: str
    source_crs: str
    target_crs: str
    accuracy_m: float | None
    scope: Literal["stored", "request"] = "stored"

    def matches(self, operation: Operation, source: CRS, target: CRS) -> bool:
        return (
            self.operation_id == operation.id
            and self.source_crs == _label(source)
            and self.target_crs == datum_label(target)
            and self.accuracy_m == operation.accuracy_m
        )


@dataclass(frozen=True)
class Resolved:
    operation: Operation
    plan: Plan
    accepted_ballpark: bool = False
    acknowledgement: Acknowledgement | None = None  # set when the operation needed one and it matched

    @property
    def pipeline(self) -> str | None:
        """The PROJ pipeline to hand to GDAL, or None when no datum change is involved (nothing to override)."""
        return None if self.operation.klass == "exact" else self.operation.pipeline


def _metres(value: float | None) -> str:
    if value is None:
        return "an unknown amount"
    return f"{value:g} m"


# --- reading what PROJ offers --------------------------------------------------------------------------------------


def _is_axis_step(step: dict[str, Any]) -> bool:
    return str(step.get("name", "")).lower().startswith("axis order change")


def _normalise_authority(identifier: dict[str, Any] | None) -> str | None:
    if not identifier or identifier.get("code") is None:
        return None
    authority = str(identifier.get("authority", ""))
    wrapped = _WRAPPED_AUTHORITY.match(authority)
    if wrapped:
        authority = wrapped[1]  # INVERSE(EPSG), DERIVED_FROM(EPSG): the same registered operation, used another way
    return f"{authority}:{identifier['code']}" if authority else None


def _core_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    steps = data.get("steps") or [data]
    core = [s for s in steps if not _is_axis_step(s)]
    return core or steps


def _identity(name: str, klass: str, code: str | None, pipeline: str | None) -> str:
    if klass == "exact":
        return EXACT_ID
    if klass == "ballpark":
        return BALLPARK_ID
    if code:
        return code
    return "sha:" + hashlib.sha256((pipeline or name).encode("utf-8")).hexdigest()[:12]


def _from_transformer(transformer: Transformer) -> Operation:
    try:
        data = json.loads(transformer.to_json())
    except (ValueError, ProjError):
        data = {"name": transformer.description}
    core = _core_steps(data)
    name = " + ".join(str(s.get("name", "")).strip() for s in core if s.get("name")) or transformer.description
    code = next(
        (c for s in core if s.get("type") == "Transformation" and (c := _normalise_authority(s.get("id")))),
        None,
    )
    accuracy = transformer.accuracy
    signal = _ballpark_signal(transformer, name)
    if signal is not None:
        klass: Klass = "ballpark"
        accuracy_m = None
    elif accuracy is None or accuracy < 0:
        # A registered operation that states no accuracy is not a ballpark. It is approximate with an unknown error,
        # and is never used without a person acknowledging that.
        klass, accuracy_m = "approximate", None
    elif accuracy == 0:
        klass, accuracy_m = "exact", 0.0
    else:
        klass, accuracy_m = "approximate", float(accuracy)
    area = getattr(transformer, "area_of_use", None)
    pipeline = transformer.definition
    return Operation(
        id=_identity(name, klass, code, pipeline),
        name=name,
        klass=klass,
        accuracy_m=accuracy_m,
        authority_code=code,
        area_of_use=getattr(area, "name", None) if area else None,
        pipeline=pipeline,
        transformer=transformer,
        ballpark_signal=signal,
    )


def _ballpark_signal(transformer: Transformer, name: str) -> str | None:
    """How this operation was recognised as a ballpark, or None when it is not one.

    The structural answer comes from PROJ itself (`has_ballpark_transformation` on any step), so renaming an operation
    cannot hide a ballpark. The wording ("allpark" in the name) is only corroboration. When the two disagree the
    operation is still treated as a ballpark, because blocking a good operation is recoverable and using a bad one is
    not, and the disagreement is logged so it can be looked into."""
    steps = getattr(transformer, "operations", None) or ()
    flags = [getattr(step, "has_ballpark_transformation", None) for step in steps]
    structural = any(flag is True for flag in flags)
    text = "allpark" in (transformer.description or "") or "allpark" in name
    if structural and text:
        return "both"
    if structural:
        log.warning("operation %r is a ballpark by structure but its wording does not say so", name)
        return "structural"
    if text:
        # No structural flag was reported (none of the steps said True): fail closed on the wording.
        log.warning("operation %r says ballpark in its wording but PROJ reports no ballpark step", name)
        return "text"
    return None


def _from_unavailable(operation: Any) -> Operation:
    code = None
    try:
        code = _normalise_authority(operation.to_json_dict().get("id"))
    except (ValueError, ProjError, AttributeError):
        pass
    accuracy = operation.accuracy
    area = getattr(operation, "area_of_use", None)
    grids = tuple(Grid(g.short_name, bool(g.available)) for g in operation.grids)
    return Operation(
        id=_identity(operation.name, "unavailable", code, operation.name),
        name=operation.name,
        klass="unavailable",
        accuracy_m=float(accuracy) if accuracy is not None and accuracy >= 0 else None,
        authority_code=code,
        area_of_use=getattr(area, "name", None) if area else None,
        grids=grids,
    )


@lru_cache(maxsize=256)
def _crs_from_text(text: str) -> CRS:
    return CRS.from_user_input(text)


def _as_crs(value: CRS | str) -> CRS:
    return value if isinstance(value, CRS) else _crs_from_text(value)


def crs_from_wkt(wkt: str) -> CRS:
    """Parse a stored WKT once; repeated calls (one per map tile) reuse the result."""
    return _crs_from_text(wkt)


def _label(crs: CRS) -> str:
    authority = crs.to_authority(min_confidence=100)
    return f"{authority[0]}:{authority[1]}" if authority else crs.name


crs_label = _label  # public name, for the callers that build an Acknowledgement


@lru_cache(maxsize=256)
def _group(source_wkt: str, target_wkt: str, aoi: tuple[float, float, float, float] | None) -> tuple[Operation, ...]:
    area = AreaOfInterest(*aoi) if aoi else None
    with warnings.catch_warnings():
        # pyproj warns when the best operation needs a missing grid. That is reported by the plan, not as a warning.
        warnings.simplefilter("ignore")
        group = TransformerGroup(
            CRS.from_wkt(source_wkt),
            CRS.from_wkt(target_wkt),
            always_xy=True,
            area_of_interest=area,
            allow_ballpark=True,
        )
    operations: list[Operation] = []
    seen: set[tuple[str, str | None]] = set()
    for transformer in group.transformers:
        operation = _from_transformer(transformer)
        key = (operation.id, operation.pipeline)
        if key not in seen:
            seen.add(key)
            operations.append(operation)
    for unavailable in group.unavailable_operations:
        operation = _from_unavailable(unavailable)
        key = (operation.id, operation.name)
        if key not in seen:
            seen.add(key)
            operations.append(operation)
    return tuple(operations)


def _clean_bounds(bounds: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
    if bounds is None:
        return None
    west, south, east, north = (float(v) for v in bounds)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None  # antimeridian or degenerate boxes: do not narrow the search
    return (round(west, 4), round(south, 4), round(east, 4), round(north, 4))


def plan(source: CRS | str, target: CRS | str, bounds: tuple[float, float, float, float] | None = None) -> Plan:
    """Every operation PROJ offers from `source` to `target` for the area `bounds` (west, south, east, north in
    degrees; omit it to list all registered operations for the pair), classified."""
    return _plan(_as_crs(source), _as_crs(target), _clean_bounds(bounds))


@lru_cache(maxsize=512)
def _plan(source_crs: CRS, target_crs: CRS, aoi: tuple[float, float, float, float] | None) -> Plan:
    operations = list(_group(source_crs.to_wkt(), target_crs.to_wkt(), aoi))
    usable = [o for o in operations if o.usable]
    exact = [o for o in usable if o.klass == "exact"]
    approximate = [o for o in usable if o.klass == "approximate"]
    messages: list[str] = []

    if exact:
        status: PlanStatus = "exact"
        recommended: str | None = exact[0].id
    elif len(approximate) == 1:
        status, recommended = "approximate", approximate[0].id
    elif len(approximate) > 1:
        status = "selection_required"
        recommended = min(approximate, key=lambda o: o.accuracy_m if o.accuracy_m is not None else float("inf")).id
        messages.append(
            f"{len(approximate)} registered operations apply. They differ in method, area and accuracy, and none is "
            "chosen for you. The one with the smallest documented accuracy is only a suggestion."
        )
    else:
        status, recommended = "unavailable", None

    grids = []
    for operation in operations:
        if operation.klass == "unavailable":
            grids.extend(g.name for g in operation.grids if not g.available)
    if grids and status != "exact":
        messages.append(
            "Operations that need these datum-shift grids are listed but cannot be used because the grids are not "
            f"installed: {', '.join(dict.fromkeys(grids))}."
        )
    if any(o.klass == "ballpark" for o in operations) and status != "exact":
        messages.append(
            "A ballpark fallback exists for this pair. It has no documented accuracy and is blocked unless you "
            "explicitly accept it."
        )
    if status == "unavailable" and not operations:
        messages.append("PROJ offers no operation between these two CRSs.")
    return Plan(_label(source_crs), _label(target_crs), status, operations, recommended, messages)


def locate(crs: CRS | str, xs: Any, ys: Any, pad_degrees: float = 0.01) -> tuple[float, float, float, float] | None:
    """A rough longitude/latitude box around native coordinates. It is used only to decide which regional operations
    apply, never to produce a result, so PROJ's default operation is good enough here."""
    try:
        lon, lat = Transformer.from_crs(_as_crs(crs), "EPSG:4326", always_xy=True).transform(xs, ys)
    except (ProjError, CRSError):
        return None
    try:
        lons, lats = [float(v) for v in lon], [float(v) for v in lat]
    except TypeError:
        lons, lats = [float(lon)], [float(lat)]
    if not lons or not all(abs(v) <= 180 for v in lons) or not all(abs(v) <= 90 for v in lats):
        return None
    return (
        max(-180.0, min(lons) - pad_degrees),
        max(-90.0, min(lats) - pad_degrees),
        min(180.0, max(lons) + pad_degrees),
        min(90.0, max(lats) + pad_degrees),
    )


# --- choosing one ---------------------------------------------------------------------------------------------------


def _describe_candidates(candidates: list[Operation]) -> str:
    return "; ".join(f"{o.id} ({o.name}, {o.summary()})" for o in candidates)


def resolve(
    source: CRS | str,
    target: CRS | str,
    bounds: tuple[float, float, float, float] | None = None,
    operation_id: str | None = None,
    accept_ballpark: bool = False,
    acknowledgement: Acknowledgement | None = None,
) -> Resolved:
    """The operation to use, or a `TransformationError` that says why none can be used yet.

    A ballpark operation is returned only when it was asked for by id (or is the only one) *and* `accept_ballpark` is
    true. A plan with several approximate operations is refused until `operation_id` names one of them. An approximate
    operation whose accuracy is worse than `ACK_ACCURACY_M`, or not stated, is refused until `acknowledgement` matches
    it exactly (same operation, CRSs and accuracy value).
    """
    result = plan(source, target, bounds)
    src, dst = _as_crs(source), _as_crs(target)
    if operation_id:
        chosen = result.find(operation_id)
        if chosen is None:
            raise TransformationError(
                422,
                "TRANSFORMATION_UNKNOWN_OPERATION",
                "That operation does not apply here",
                f"'{operation_id}' is not one of the operations for this pair of CRSs and area. Valid choices: "
                f"{', '.join(o.id for o in result.operations) or 'none'}.",
                result,
            )
        if chosen.klass == "unavailable":
            raise _grid_error(result, [chosen])
        if chosen.klass == "ballpark" and not accept_ballpark:
            raise _ballpark_error(result)
        return Resolved(
            chosen,
            result,
            accepted_ballpark=chosen.klass == "ballpark",
            acknowledgement=_gate(chosen, result, src, dst, acknowledgement),
        )

    if result.status in ("exact", "approximate"):
        only = result.usable[0]
        return Resolved(only, result, acknowledgement=_gate(only, result, src, dst, acknowledgement))
    if result.status == "selection_required":
        approximate = [o for o in result.usable if o.klass == "approximate"]
        raise TransformationError(
            409,
            "TRANSFORMATION_SELECTION_REQUIRED",
            "Choose a transformation",
            f"{len(approximate)} registered operations apply and they give different positions. Choose one: "
            f"{_describe_candidates(approximate)}.",
            result,
        )
    ballpark = [o for o in result.operations if o.klass == "ballpark"]
    if ballpark and accept_ballpark:
        return Resolved(ballpark[0], result, accepted_ballpark=True)
    missing = [o for o in result.operations if o.klass == "unavailable"]
    if missing:
        raise _grid_error(result, missing)
    if ballpark:
        raise _ballpark_error(result)
    raise TransformationError(
        409,
        "TRANSFORMATION_UNAVAILABLE",
        "No transformation is available",
        "PROJ offers no coordinate operation between these two CRSs for this area.",
        result,
    )


def datum_label(crs: CRS) -> str:
    """The label of the geodetic CRS under `crs`. A datum shift is the same whether it ends in longitude/latitude or in
    Web Mercator, so an acknowledgement of a shift to WGS 84 also covers the tiles drawn in Web Mercator."""
    return _label(crs.geodetic_crs or crs)


def _gate(
    chosen: Operation, result: Plan, source: CRS, target: CRS, acknowledgement: Acknowledgement | None
) -> Acknowledgement | None:
    """The acknowledgement that allowed `chosen`, None when none was needed; raises when one is needed and missing or
    stale. A ballpark never gets here, and nothing a caller supplies can make a ballpark or a missing grid usable."""
    reason = chosen.ack_reason
    if reason is None:
        return None
    if acknowledgement is not None and acknowledgement.matches(chosen, source, target):
        return acknowledgement
    raise _acknowledgement_error(chosen, result, source, target, reason, stale=acknowledgement is not None)


def _acknowledgement_error(
    chosen: Operation, result: Plan, source: CRS, target: CRS, reason: str, *, stale: bool
) -> TransformationError:
    if reason == ACK_NOT_STATED:
        why = "its accuracy is not stated anywhere in the registry, so its error could be any size"
    else:
        why = (
            f"its documented accuracy is about {_metres(chosen.accuracy_m)}, which is worse than the "
            f"{ACK_ACCURACY_M:g} m limit for using a datum shift without acknowledgement"
        )
    lead = (
        "An earlier acknowledgement no longer matches this operation (the operation, the CRSs or its accuracy changed)"
        if stale
        else "This datum shift needs to be acknowledged before it is used"
    )
    return TransformationError(
        409,
        ACK_REQUIRED_CODE,
        "Acknowledge the transformation accuracy",
        f"{lead}: {chosen.name} ({chosen.id}); {why}. Nothing was converted. An acknowledgement can only be recorded "
        "together with a new georeferencing version or an area of interest; a raster georeferenced by its own file, "
        "and a version recorded before acknowledgements existed, stay blocked because their history cannot be "
        "amended.",
        result,
        extra={
            "operation_id": chosen.id,
            "operation_name": chosen.name,
            "accuracy_m": chosen.accuracy_m,
            "threshold_m": ACK_ACCURACY_M,
            "reason": reason,
            "source_crs": _label(source),
            "target_crs": datum_label(target),
            "stale_acknowledgement": stale,
        },
    )


def _grid_error(result: Plan, operations: list[Operation]) -> TransformationError:
    names = list(dict.fromkeys(g.name for o in operations for g in o.grids if not g.available))
    return TransformationError(
        409,
        "TRANSFORMATION_GRID_MISSING",
        "A required datum-shift grid is not installed",
        f"The registered operation needs {', '.join(names) or 'a grid'}, which is not available offline. Nothing was "
        "downloaded and no substitute was used. Install the grid from an approved source, or use a different CRS.",
        result,
    )


def _ballpark_error(result: Plan) -> TransformationError:
    return TransformationError(
        409,
        "TRANSFORMATION_BALLPARK_BLOCKED",
        "Only a rough ballpark transformation is available",
        "The datum shift for these two CRSs is not defined well enough to give a trustworthy position: PROJ would "
        "assume the datums nearly coincide and state no accuracy. It is blocked. The error can be tens to hundreds of "
        "metres. Use a CRS with a registered datum shift, or accept the ballpark explicitly.",
        result,
    )


def transform_bounds_with(
    pipeline: str, bounds: tuple[float, float, float, float], direction: str = "inverse"
) -> tuple[float, float, float, float]:
    """Bounding box of a box mapped through a PROJ pipeline, by densified edges (for choosing a source window)."""
    import numpy as np

    left, bottom, right, top = bounds
    steps = np.linspace(0.0, 1.0, 21)
    xs = np.concatenate(
        [left + steps * (right - left), np.full(21, right), right - steps * (right - left), np.full(21, left)]
    )
    ys = np.concatenate(
        [np.full(21, bottom), bottom + steps * (top - bottom), np.full(21, top), top - steps * (top - bottom)]
    )
    transformer = Transformer.from_pipeline(pipeline)
    out_x, out_y = transformer.transform(
        xs, ys, direction=TransformDirection.INVERSE if direction == "inverse" else TransformDirection.FORWARD
    )
    return float(np.min(out_x)), float(np.min(out_y)), float(np.max(out_x)), float(np.max(out_y))
