"""The one place coordinates are converted between coordinate reference systems.

Every conversion that moves positions between two CRSs goes through `convert`, which asks the transformation planner
(`geopoc.geo.transform`) which operation may be used and then applies exactly that operation. So the same coordinates
give the same answer wherever they are converted, and the same refusals apply everywhere:

* no datum change: exact, used without asking;
* one registered datum shift: used, with its documented accuracy, if that accuracy is within 10 m. A shift that is
  less accurate, or whose accuracy is not stated, is refused until the person has acknowledged that specific shift
  (`TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED`);
* several registered datum shifts: refused until one is chosen (`TRANSFORMATION_SELECTION_REQUIRED`);
* only a ballpark fallback: refused (`TRANSFORMATION_BALLPARK_BLOCKED`);
* a registered shift that needs a grid that is not installed: refused, naming the grid (`TRANSFORMATION_GRID_MISSING`).

Nothing falls back to PROJ's default operation. The result carries the provenance of the operation actually used.

What is deliberately not here: conversions that never change datum and are not positions on the earth (Web Mercator tile
arithmetic from WGS 84, pixel <-> native coordinates through an affine geotransform, geodesic measurement on a CRS's own
ellipsoid, degree <-> metre size estimates). Those are labelled where they occur.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pyproj
from pyproj import CRS
from pyproj.exceptions import ProjError

from geopoc.geo import transform as transforms
from geopoc.geo.transform import TransformationError

GEOGRAPHIC = "EPSG:4326"
IDENTITY_ID = "identity"

# Why a conversion could not be done, for the places that report instead of failing.
TRANSFORM_FAILED = "TRANSFORM_FAILED"


@dataclass(frozen=True)
class Converted:
    """Converted coordinates and the operation that produced them."""

    x: Any
    y: Any
    provenance: dict[str, Any]
    resolved: transforms.Resolved | None  # None when source and target are the same CRS (nothing to convert)

    @property
    def classification(self) -> str:
        return self.provenance["operation"]["classification"]


def recorded_operation(raster: Any) -> str | None:
    """The datum transformation chosen when a person recorded this raster's georeferencing, if any (kept in that
    version's validation record, so a historical choice stays readable)."""
    assertion = getattr(raster, "current_assertion", None)
    if assertion is None or assertion.operation == "revert_to_file":
        return None
    operation = ((assertion.validation or {}).get("transformation") or {}).get("operation") or {}
    return operation.get("id")


def operation_for(raster: Any, source: str, requested: str | None) -> str | None:
    """The operation id to use for a raster: the choice recorded with its georeferencing wins; otherwise the one
    requested for this call (a file-georeferenced raster carries no stored choice)."""
    if source == "recorded":
        return recorded_operation(raster) or requested
    return requested


def recorded_acknowledgement(raster: Any) -> transforms.Acknowledgement | None:
    """The acknowledgement recorded with this raster's current georeferencing version, if there is one.

    It lives in that version's validation record (`transformation.acknowledgement`), so it is as permanent as the
    version. A file-georeferenced raster has no version and so never has one; a version recorded before
    acknowledgements existed has none either. Neither can be given one afterwards (history is append-only), so a raster
    that needs one in either state stays blocked."""
    assertion = getattr(raster, "current_assertion", None)
    if assertion is None or assertion.operation == "revert_to_file":
        return None
    return acknowledgement_from_record((assertion.validation or {}).get("transformation"))


def acknowledgement_from_record(record: dict[str, Any] | None) -> transforms.Acknowledgement | None:
    """An acknowledgement from a stored provenance record (a georeferencing version's or an AOI's), or None."""
    stored = (record or {}).get("acknowledgement")
    if not isinstance(stored, dict):
        return None
    try:
        return transforms.Acknowledgement(
            operation_id=str(stored["operation_id"]),
            source_crs=str(stored["source_crs"]),
            target_crs=str(stored["target_crs"]),
            accuracy_m=None if stored.get("accuracy_m") is None else float(stored["accuracy_m"]),
            scope="stored",
        )
    except (KeyError, TypeError, ValueError):
        return None  # a malformed record acknowledges nothing


@dataclass(frozen=True)
class Selection:
    """Which operation to use for a stored raster and the acknowledgement that goes with it."""

    operation_id: str | None
    acknowledgement: transforms.Acknowledgement | None


def selection_for(raster: Any, source: str, requested: str | None) -> Selection:
    """The operation and acknowledgement to use for a raster's stored georeferencing.

    The operation follows `operation_for`. The acknowledgement comes only from what was recorded with the georeferencing
    version in use. Nothing in a request can supply one for stored data: a request can choose between operations, it
    cannot accept an accuracy on the raster's behalf."""
    operation_id = operation_for(raster, source, requested)
    acknowledgement = recorded_acknowledgement(raster) if source == "recorded" else None
    return Selection(operation_id, acknowledgement)


def provenance(
    source_label: str, target_label: str, resolved: transforms.Resolved | None, operation_id: str | None
) -> dict[str, Any]:
    """What was used, in the same shape the footprint record has always had, plus the source CRS."""
    base = {
        "source_crs_id": source_label,
        "target_crs_id": target_label,
        "proj_version": pyproj.proj_version_str,
        "pyproj_version": pyproj.__version__,
        "network": "disabled",
    }
    if resolved is None:
        return {
            **base,
            "definition": None,
            "accuracy_m": 0.0,
            "plan_status": "exact",
            "operation": {
                "id": IDENTITY_ID,
                "name": "Same CRS: nothing is converted",
                "classification": "exact",
                "authority_code": None,
                "chosen_by": "identity",
                "ballpark_accepted": False,
                "requires_acknowledgement": False,
                "acknowledgement": None,
            },
        }
    operation = resolved.operation
    return {
        **base,
        "definition": operation.pipeline,
        "accuracy_m": operation.accuracy_m,
        "plan_status": resolved.plan.status,
        "operation": {
            "id": operation.id,
            "name": operation.name,
            "classification": operation.klass,
            "authority_code": operation.authority_code,
            "chosen_by": "selection" if operation_id else "only_option",
            "ballpark_accepted": resolved.accepted_ballpark,
            "requires_acknowledgement": operation.requires_acknowledgement,
            "acknowledgement": acknowledgement_record(resolved.acknowledgement),
        },
        "acknowledgement_threshold_m": transforms.ACK_ACCURACY_M,
    }


def acknowledgement_record(acknowledgement: transforms.Acknowledgement | None) -> dict[str, Any] | None:
    """The JSON form of an acknowledgement, without who and when (the caller that stores it adds those)."""
    if acknowledgement is None:
        return None
    return {
        "operation_id": acknowledgement.operation_id,
        "source_crs": acknowledgement.source_crs,
        "target_crs": acknowledgement.target_crs,
        "accuracy_m": acknowledgement.accuracy_m,
        "scope": acknowledgement.scope,
    }


def _label(crs: CRS) -> str:
    authority = crs.to_authority(min_confidence=100)
    return f"{authority[0]}:{authority[1]}" if authority else crs.name


def convert(
    source: CRS | str,
    target: CRS | str,
    x: Any,
    y: Any,
    *,
    operation_id: str | None = None,
    accept_ballpark: bool = False,
    bounds: tuple[float, float, float, float] | None = None,
    acknowledgement: transforms.Acknowledgement | None = None,
) -> Converted:
    """Convert coordinates (x first: easting or longitude) from `source` to `target` with the operation the planner
    allows.

    `x` and `y` may be numbers or arrays. Raises `TransformationError` when no operation may be used (it carries the
    plan, so callers can list the candidates) or when the coordinates cannot be converted.
    """
    src = source if isinstance(source, CRS) else transforms.crs_from_wkt(source)
    dst = target if isinstance(target, CRS) else transforms.crs_from_wkt(target)
    if src == dst:
        return Converted(x, y, provenance(_label(src), _label(dst), None, None), None)
    where = bounds if bounds is not None else transforms.locate(src, x, y, pad_degrees=0.05)
    resolved = transforms.resolve(src, dst, where, operation_id, accept_ballpark, acknowledgement)
    transformer = resolved.operation.transformer
    assert transformer is not None  # only usable operations resolve, and those carry a transformer
    try:
        out_x, out_y = transformer.transform(x, y)
    except ProjError as exc:
        raise TransformationError(
            422, TRANSFORM_FAILED, "The coordinates could not be converted", str(exc), resolved.plan
        ) from exc
    if not (np.all(np.isfinite(out_x)) and np.all(np.isfinite(out_y))):
        raise TransformationError(
            422,
            TRANSFORM_FAILED,
            "The coordinates are outside the domain of the target CRS",
            "No coordinates exist there, so nothing is returned.",
            resolved.plan,
        )
    return Converted(out_x, out_y, provenance(_label(src), _label(dst), resolved, operation_id), resolved)


def to_geographic(crs: CRS | str, x: Any, y: Any, **options: Any) -> Converted:
    """From a CRS to WGS 84 longitude/latitude."""
    return convert(crs, GEOGRAPHIC, x, y, **options)


def from_geographic(crs: CRS | str, lon: Any, lat: Any, **options: Any) -> Converted:
    """From WGS 84 longitude/latitude to a CRS."""
    return convert(GEOGRAPHIC, crs, lon, lat, **options)


def unavailable_reason(exc: TransformationError) -> str:
    """One sentence for a result that is left out because its conversion is not allowed."""
    return f"{exc.title}. {exc.detail}"
