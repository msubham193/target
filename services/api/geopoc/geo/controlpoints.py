"""Georeferencing a scanned map sheet from control points a person supplied.

A control point pairs a pixel position in the picture with a coordinate in a CRS the person chose. The six affine
parameters are fitted by least squares and every point's residual is reported, so a mistyped or misplaced point shows.
Nothing is invented: the points, the CRS and where the coordinates came from are the person's, and are recorded with the
result. The fit does not know the sheet's own projection. A single affine map across a sheet printed in another
projection leaves systematic residuals, which is exactly what the report is for; it is never evidence that the
sheet's projection is the chosen CRS.

How good a fit is has to be judged on the ground, not in pixels or map units: a pixel is 10 m for one image and 0.5 m
for another, a degree is not a metre, and the two pixel sides need not be equal. So everything is measured in metres
(see `geopoc.geo.orientation`) and the tolerance comes from the picture's own ground resolution:

    g      = sqrt(|u| * |v|)                     ground size of a pixel (u, v: ground vectors of a column / row step)
    sigma  = sqrt((p*g)^2 + s_ref^2 + s_src^2)   expected error of one coordinate of one point, in metres

`p` is how closely a person can place a point (one pixel by default). `s_ref` is the uncertainty of the reference
coordinates and `s_src` that of the printed source; neither is known unless the person states it, so both are zero by
default and the result says they were not stated. A 2-D residual then has an expected size of sqrt(2)*sigma; a fit is
worth a warning above twice that and fails above five times it. Only the number of degrees of freedom is allowed for:
n points fitted with six numbers leave 2n - 6 free observations.
"""

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyproj
from shapely.geometry import MultiPoint

from geopoc.geo import orientation
from geopoc.geo.georeference import Check

METHOD = "affine_least_squares"
EVIDENCE_SCHEMA = 2
MIN_POINTS = 3

# Defaults of the tolerance model. `CLICK_PIXELS` is a person placing a point on a picture.
CLICK_PIXELS = 1.0
WARN_FACTOR = 2.0  # of the expected 2-D error, sqrt(2) * sigma
FAIL_FACTOR = 5.0
WARN_ROTATION_DEGREES = 5.0
WARN_SCALE_RATIO = 1.05
FAIL_SCALE_RATIO = 1.25
WARN_HULL_FRACTION = 0.25
WARN_COLLINEAR_EXTENT = 0.05  # short over long principal extent of the points in the picture
MAX_CONDITION = 1e8
LEVERAGE_LIMIT = 1.0 - 1e-9  # a point this influential cannot be left out and predicted

EXACT_FIT_CODE = "EXACT_FIT_NO_CHECK"


@dataclass
class Point:
    label: str
    column: float
    row: float
    x: float
    y: float


@dataclass
class Fit:
    geotransform: list[float]
    residuals: list[dict[str, Any]]
    rms_pixels: float
    max_pixels: float
    rms_map_units: float
    map_units: str | None
    pixel_size: list[float]
    rotation_degrees: float
    scale_ratio: float
    redundant: bool
    rms_m: float | None
    checks: list[Check] = field(default_factory=list)
    # --- ground-metre assessment (evidence schema 2) ---
    max_m: float | None = None
    rms_m_dof: float | None = None  # sqrt(sum r^2 / (n - 3)): comparable with sqrt(2)*sigma; None with three points
    ground_pixel_m: list[float] = field(default_factory=list)  # column step, row step
    ground_resolution_m: float | None = None  # g
    shear_degrees: float | None = None
    shear_effect_m: float | None = None
    handedness: str | None = None
    rotation_reference: str | None = None
    tolerance: dict[str, Any] | None = None
    leave_one_out: dict[str, Any] | None = None
    corner_uncertainty_m: float | None = None
    corners: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.status == "failed" for c in self.checks)


def _check(checks: list[Check], code: str, status: str, message: str) -> None:
    checks.append(Check(code, status, message))  # type: ignore[arg-type]


def _names(points: list[Point], indexes: list[int]) -> str:
    return ", ".join(points[i].label or f"#{i + 1}" for i in indexes)


def _inputs(points: list[Point], width: int, height: int, checks: list[Check]) -> bool:
    """Reject points that cannot be used. Returns False when the fit must not be attempted."""
    numbers = [v for p in points for v in (p.column, p.row, p.x, p.y)]
    if not all(math.isfinite(v) for v in numbers):
        _check(
            checks, "CONTROL_POINTS_FINITE", "failed", "Every pixel position and coordinate must be a finite number."
        )
        return False
    if len(points) < MIN_POINTS:
        _check(
            checks,
            "CONTROL_POINTS_COUNT",
            "failed",
            f"At least {MIN_POINTS} control points are needed (six affine numbers, two per point); "
            f"{len(points)} given.",
        )
        return False
    outside = [
        p.label or f"#{i + 1}" for i, p in enumerate(points) if not (0 <= p.column <= width and 0 <= p.row <= height)
    ]
    if outside:
        _check(
            checks,
            "CONTROL_POINTS_INSIDE_IMAGE",
            "failed",
            f"Point(s) {', '.join(outside)} lie outside the picture ({width} x {height} pixels).",
        )
        return False
    duplicated: list[int] = []
    seen_pixels: dict[tuple[float, float], int] = {}
    seen_ground: dict[tuple[float, float], int] = {}
    for i, p in enumerate(points):
        for table, key in (
            (seen_pixels, (round(p.column, 3), round(p.row, 3))),
            (seen_ground, (round(p.x, 9), round(p.y, 9))),
        ):
            if key in table:
                duplicated.extend(j for j in (table[key], i) if j not in duplicated)
            else:
                table[key] = i
    if duplicated:
        _check(
            checks,
            "CONTROL_POINTS_DISTINCT",
            "failed",
            f"Point(s) {_names(points, sorted(duplicated))} share the same pixel position or the same coordinate "
            "with another point. Each point must be distinct.",
        )
        return False
    _check(checks, "CONTROL_POINTS_COUNT", "ok", f"{len(points)} control points, all inside the picture.")
    return True


def _normalised(design: np.ndarray, width: int, height: int) -> np.ndarray:
    """The design matrix with pixel columns scaled to 0..1, so its condition number does not depend on image size."""
    return design * np.array([1.0, 1.0 / width, 1.0 / height])


def _metre_errors(basis: orientation.Basis, delta: np.ndarray) -> np.ndarray:
    return np.hypot(*basis.to_metres(delta[:, 0], delta[:, 1]).T)


def fit(
    points: list[Point],
    width: int,
    height: int,
    crs: pyproj.CRS,
    *,
    reference_uncertainty_m: float | None = None,
    source_uncertainty_m: float | None = None,
    click_pixels: float = CLICK_PIXELS,
) -> Fit | None:
    """Fit pixel -> coordinate by least squares and judge the fit. Returns a failed `Fit` when the points cannot be
    fitted. `reference_uncertainty_m` and `source_uncertainty_m` are the person's statement of how uncertain the
    reference coordinates and the printed source are (None: not stated, taken as zero and reported as such)."""
    checks: list[Check] = []
    if not _inputs(points, width, height, checks):
        return _failed(checks)
    design = np.array([[1.0, p.column, p.row] for p in points])
    if np.linalg.cond(_normalised(design, width, height)) > MAX_CONDITION:
        _check(
            checks,
            "CONTROL_POINTS_SPREAD",
            "failed",
            "The points lie on one line in the picture, so they cannot fix the sheet's position and rotation. "
            "Spread them across the sheet.",
        )
        return _failed(checks)
    targets = np.array([[p.x, p.y] for p in points])
    n = len(points)
    solution, *_ = np.linalg.lstsq(design, targets, rcond=None)  # rows: [x0, y0], [x_c, y_c], [x_r, y_r]
    gt = [
        float(solution[0, 0]),
        float(solution[1, 0]),
        float(solution[2, 0]),
        float(solution[0, 1]),
        float(solution[1, 1]),
        float(solution[2, 1]),
    ]
    linear = np.array([[gt[1], gt[2]], [gt[4], gt[5]]])
    determinant = float(np.linalg.det(linear))
    if not math.isfinite(determinant) or determinant == 0:
        _check(checks, "CONTROL_POINTS_FIT", "failed", "The points give a degenerate transform (zero pixel area).")
        return _failed(checks)

    predicted = design @ solution
    delta = targets - predicted
    inverse = np.linalg.inv(linear)
    pixel_error = np.hypot(*(inverse @ delta.T))
    map_error = np.hypot(delta[:, 0], delta[:, 1])

    # --- the same, on the ground ---
    xs, ys = targets[:, 0], targets[:, 1]
    basis = orientation.local_basis(crs, xs, ys)
    centre = orientation.local_basis(crs, np.array([xs.mean()]), np.array([ys.mean()]))
    if basis is None or centre is None:
        _check(
            checks,
            "CONTROL_POINTS_DOMAIN",
            "failed",
            f"Some coordinates are outside the area where {crs.name} is defined, so their size on the ground cannot "
            "be measured. Check the CRS and the coordinates.",
        )
        return _failed(checks)
    ground = orientation.geometry(crs, gt, centre.ex[0], centre.ey[0])
    metre_error = _metre_errors(basis, delta)
    rms_m = float(np.sqrt(np.mean(metre_error**2)))
    max_m = float(metre_error.max())
    rms_dof = float(np.sqrt(np.sum(metre_error**2) / (n - 3))) if n > 3 else None

    g = ground.ground_pixel_m
    stated = {"reference": reference_uncertainty_m is not None, "source": source_uncertainty_m is not None}
    s_ref, s_src = reference_uncertainty_m or 0.0, source_uncertainty_m or 0.0
    sigma = math.sqrt((click_pixels * g) ** 2 + s_ref**2 + s_src**2)
    expected = math.sqrt(2.0) * sigma  # expected size of a 2-D residual
    warn_m, fail_m = WARN_FACTOR * expected, FAIL_FACTOR * expected
    tolerance = {
        "formula": "sigma = sqrt((p*g)^2 + s_ref^2 + s_src^2); a 2-D residual is expected to be sqrt(2)*sigma",
        "ground_resolution_m": g,
        "click_pixels": click_pixels,
        "reference_uncertainty_m": reference_uncertainty_m,
        "source_uncertainty_m": source_uncertainty_m,
        "reference_uncertainty_stated": stated["reference"],
        "source_uncertainty_stated": stated["source"],
        "sigma_m": sigma,
        "expected_residual_m": expected,
        "warn_above_m": warn_m,
        "fail_above_m": fail_m,
        "warn_factor": WARN_FACTOR,
        "fail_factor": FAIL_FACTOR,
    }

    size_x, size_y = math.hypot(gt[1], gt[4]), math.hypot(gt[2], gt[5])
    rotation = ground.rotation_degrees
    redundant = n > MIN_POINTS
    units = crs.axis_info[0].unit_name if crs.axis_info else None

    gram_inverse = np.linalg.inv(design.T @ design)
    leverage = np.einsum("ij,jk,ik->i", design, gram_inverse, design)
    loo = _leave_one_out(points, design, targets, basis, leverage, expected, width, height)

    residuals = [
        {
            "label": p.label or f"#{i + 1}",
            "column": p.column,
            "row": p.row,
            "x": p.x,
            "y": p.y,
            "fitted_x": float(predicted[i, 0]),
            "fitted_y": float(predicted[i, 1]),
            "residual_map_units": float(map_error[i]),
            "residual_pixels": float(pixel_error[i]),
            "residual_m": float(metre_error[i]),
            "leverage": float(leverage[i]),
            "leave_one_out_m": loo["per_point"][i]["leave_one_out_m"],
            "leave_one_out_ratio": loo["per_point"][i]["ratio"],
        }
        for i, p in enumerate(points)
    ]

    if not redundant:
        _check(
            checks,
            EXACT_FIT_CODE,
            "warning",
            "Exactly three points fit the six numbers with no residual, so the fit cannot reveal a wrong point and "
            "there is no way to check its accuracy. A residual of zero here means nothing. Add at least one more "
            "point.",
        )
    elif rms_dof is not None and rms_dof > fail_m:
        _check(
            checks,
            "CONTROL_POINTS_RESIDUALS",
            "failed",
            f"The fit does not agree with the points: residual {rms_dof:.1f} m (RMS allowing for the fit), worst "
            f"{max_m:.1f} m, where about {expected:.1f} m is expected from a {g:.2f} m pixel. Check the coordinates, "
            "the pixel positions and the CRS. Nothing is recorded.",
        )
    elif rms_dof is not None and rms_dof > warn_m:
        worst = residuals[int(np.argmax(metre_error))]["label"]
        _check(
            checks,
            "CONTROL_POINTS_RESIDUALS",
            "warning",
            f"Residual {rms_dof:.1f} m (RMS allowing for the fit), worst {max_m:.1f} m at {worst}; about "
            f"{expected:.1f} m is expected from a {g:.2f} m pixel. A point may be misplaced, or the sheet's "
            "projection differs from the CRS you chose.",
        )
    else:
        _check(
            checks,
            "CONTROL_POINTS_RESIDUALS",
            "ok",
            f"Residual {rms_dof if rms_dof is not None else 0.0:.2f} m (RMS allowing for the fit), worst "
            f"{max_m:.2f} m: the points agree with a single affine map to within what a {g:.2f} m pixel allows.",
        )

    if loo["status"] == "complete" and loo["worst_ratio"] is not None and loo["worst_ratio"] > WARN_FACTOR:
        _check(
            checks,
            "CONTROL_POINTS_LEAVE_ONE_OUT",
            "warning",
            f"Leaving {loo['worst_label']} out, the other points put it {loo['worst_m']:.1f} m from where you "
            "placed it "
            f"({loo['worst_ratio']:.1f} times what is expected). It may be misplaced.",
        )
    elif loo["status"] == "partial":
        _check(
            checks,
            "CONTROL_POINTS_LEAVE_ONE_OUT",
            "warning",
            f"Without {', '.join(loo['essential'])} the remaining points cannot fix the sheet, so that point cannot be "
            "checked against the others and nothing else can vouch for it.",
        )

    hull = MultiPoint([(p.column, p.row) for p in points]).convex_hull.area / float(width * height)
    if hull < WARN_HULL_FRACTION:
        _check(
            checks,
            "CONTROL_POINTS_SPREAD",
            "warning",
            f"The points cover {hull:.0%} of the picture. Positions outside them are extrapolated and can be far off.",
        )
    else:
        _check(checks, "CONTROL_POINTS_SPREAD", "ok", f"The points span {hull:.0%} of the picture.")
    centred = np.array([[p.column, p.row] for p in points]) - np.mean([[p.column, p.row] for p in points], axis=0)
    eigenvalues = np.sort(np.linalg.eigvalsh(centred.T @ centred))
    extent_ratio = math.sqrt(max(eigenvalues[0], 0.0) / eigenvalues[1]) if eigenvalues[1] > 0 else 0.0
    if extent_ratio < WARN_COLLINEAR_EXTENT:
        _check(
            checks,
            "CONTROL_POINTS_COLLINEAR",
            "warning",
            f"The points are almost on one line in the picture (width across the line {extent_ratio:.0%} of its "
            "length). The sheet's rotation and scale across that line are poorly fixed.",
        )

    if ground.handedness == "mirrored":
        _check(
            checks,
            "CONTROL_POINTS_ORIENTATION",
            "warning",
            "The fitted transform turns the picture over: on the ground its rows run north, where a map's rows run "
            "south. The picture appears mirrored. Check the pixel rows entered.",
        )
    ratio = ground.anisotropy
    if ratio > FAIL_SCALE_RATIO:
        _check(
            checks,
            "CONTROL_POINTS_SCALE",
            "failed",
            f"The fitted pixel is {ratio:.2f} times longer on the ground in one direction than the other, which a map "
            "scan does not do. A point is probably wrong.",
        )
    elif ratio > WARN_SCALE_RATIO:
        _check(
            checks,
            "CONTROL_POINTS_SCALE",
            "warning",
            f"The fitted pixel is {ratio:.2f} times longer on the ground in one direction.",
        )
    shear_effect = max(width, height) * g * math.tan(math.radians(ground.shear_degrees))
    if shear_effect > fail_m:
        _check(
            checks,
            "CONTROL_POINTS_SHEAR",
            "failed",
            f"The fitted pixel's sides are {ground.shear_degrees:.2f}° from perpendicular on the ground, which "
            "moves the "
            f"far edge of the sheet by {shear_effect:.1f} m. A map scan is not skewed like that: a point is probably "
            "wrong.",
        )
    elif shear_effect > warn_m:
        _check(
            checks,
            "CONTROL_POINTS_SHEAR",
            "warning",
            f"The fitted pixel's sides are {ground.shear_degrees:.2f}° from perpendicular on the ground, which "
            "moves the "
            f"far edge of the sheet by {shear_effect:.1f} m.",
        )
    if rotation is not None and abs(rotation) > WARN_ROTATION_DEGREES:
        _check(
            checks,
            "CONTROL_POINTS_ROTATION",
            "warning",
            f"For information: the scan is rotated {rotation:.1f}° relative to {ground.rotation_reference}. "
            "A scan laid "
            "slightly crooked is normal; the full rotation is applied.",
        )

    corners = []
    for label, column, row in (
        ("top-left", 0.0, 0.0),
        ("top-right", float(width), 0.0),
        ("bottom-right", float(width), float(height)),
        ("bottom-left", 0.0, float(height)),
    ):
        h = float(np.array([1.0, column, row]) @ gram_inverse @ np.array([1.0, column, row]))
        corners.append({"corner": label, "uncertainty_m": expected * math.sqrt(h)})

    return Fit(
        geotransform=gt,
        residuals=residuals,
        rms_pixels=float(np.sqrt(np.mean(pixel_error**2))),
        max_pixels=float(pixel_error.max()),
        rms_map_units=float(np.sqrt(np.mean(map_error**2))),
        map_units=units,
        pixel_size=[size_x, size_y],
        rotation_degrees=rotation if rotation is not None else 0.0,
        scale_ratio=ratio,
        redundant=redundant,
        rms_m=rms_m,
        checks=checks,
        max_m=max_m,
        rms_m_dof=rms_dof,
        ground_pixel_m=[ground.column_m, ground.row_m],
        ground_resolution_m=g,
        shear_degrees=ground.shear_degrees,
        shear_effect_m=shear_effect,
        handedness=ground.handedness,
        rotation_reference=ground.rotation_reference,
        tolerance=tolerance,
        leave_one_out=loo,
        corner_uncertainty_m=max(c["uncertainty_m"] for c in corners),
        corners=corners,
    )


def _leave_one_out(
    points: list[Point],
    design: np.ndarray,
    targets: np.ndarray,
    basis: orientation.Basis,
    leverage: np.ndarray,
    expected_m: float,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Refit without each point in turn and measure how far the others put it from where it was placed.

    With three points leaving one out leaves too few to fit, so nothing is computed ("unavailable"). With more, a
    point whose removal leaves the rest unable to fix the sheet (collinear, or the point carries all the information)
    is listed in `essential` and the result is "partial". The ratio compares the error with what the tolerance model
    expects for a held-out point, which is larger than for a fitted one by 1 / sqrt(1 - leverage)."""
    n = len(points)
    per_point: list[dict[str, Any]] = [{"leave_one_out_m": None, "ratio": None, "essential": False} for _ in points]
    if n <= MIN_POINTS:
        return {
            "status": "unavailable",
            "reason": f"With {n} points, leaving one out leaves {n - 1}, fewer than the {MIN_POINTS} that six numbers "
            "need. The fit is exact and cannot be checked.",
            "per_point": per_point,
            "essential": [],
            "rms_m": None,
            "max_m": None,
            "worst_label": None,
            "worst_m": None,
            "worst_ratio": None,
        }
    essential: list[str] = []
    for i in range(n):
        keep = [j for j in range(n) if j != i]
        reduced = design[keep]
        if (
            leverage[i] >= LEVERAGE_LIMIT
            or np.linalg.matrix_rank(reduced) < 3
            or np.linalg.cond(_normalised(reduced, width, height)) > MAX_CONDITION
        ):
            per_point[i]["essential"] = True
            essential.append(points[i].label or f"#{i + 1}")
            continue
        sol, *_ = np.linalg.lstsq(reduced, targets[keep], rcond=None)
        miss = targets[i] - design[i] @ sol
        error = float(_metre_errors(orientation.Basis(basis.ex[i : i + 1], basis.ey[i : i + 1]), miss[None, :])[0])
        per_point[i]["leave_one_out_m"] = error
        per_point[i]["ratio"] = error * math.sqrt(1.0 - leverage[i]) / expected_m if expected_m > 0 else None
    measured = [
        (i, d["leave_one_out_m"], d["ratio"]) for i, d in enumerate(per_point) if d["leave_one_out_m"] is not None
    ]
    if not measured:
        return {
            "status": "partial",
            "reason": "No point can be left out and still leave a fit.",
            "per_point": per_point,
            "essential": essential,
            "rms_m": None,
            "max_m": None,
            "worst_label": None,
            "worst_m": None,
            "worst_ratio": None,
        }
    worst = max(measured, key=lambda m: m[2] if m[2] is not None else -1.0)
    values = np.array([m[1] for m in measured])
    return {
        "status": "partial" if essential else "complete",
        "reason": None,
        "per_point": per_point,
        "essential": essential,
        "rms_m": float(np.sqrt(np.mean(values**2))),
        "max_m": float(values.max()),
        "worst_label": points[worst[0]].label or f"#{worst[0] + 1}",
        "worst_m": float(worst[1]),
        "worst_ratio": worst[2],
    }


def _failed(checks: list[Check]) -> Fit:
    return Fit([], [], 0.0, 0.0, 0.0, None, [], 0.0, 1.0, False, None, checks)


def evidence(result: Fit, *, crs_id: str, source: str, software: str) -> dict[str, Any]:
    """What is stored with the recorded georeferencing: the points, the CRS, the source and how well they fitted."""
    return {
        "evidence_schema": EVIDENCE_SCHEMA,
        "method": METHOD,
        "crs_id": crs_id,
        "source": source,
        "point_count": len(result.residuals),
        "points": result.residuals,
        "geotransform": result.geotransform,
        "rms_pixels": result.rms_pixels,
        "max_pixels": result.max_pixels,
        "rms_map_units": result.rms_map_units,
        "map_units": result.map_units,
        "rms_m": result.rms_m,
        "max_m": result.max_m,
        "rms_m_allowing_for_fit": result.rms_m_dof,
        "redundant": result.redundant,
        "exact_fit": not result.redundant,
        "ground": {
            "pixel_m": result.ground_pixel_m,
            "resolution_m": result.ground_resolution_m,
            "anisotropy": result.scale_ratio,
            "shear_degrees": result.shear_degrees,
            "shear_effect_m": result.shear_effect_m,
            "handedness": result.handedness,
            "rotation_degrees": result.rotation_degrees,
            "rotation_reference": result.rotation_reference,
        },
        "tolerance": result.tolerance,
        "leave_one_out": {k: v for k, v in (result.leave_one_out or {}).items() if k != "per_point"},
        "corner_uncertainty_m": result.corner_uncertainty_m,
        "corners": result.corners,
        "checks": [{"code": c.code, "status": c.status} for c in result.checks],
        "software": software,
        "note": "Entered by a person. The application fitted the six numbers and measured the residuals; it did not "
        "find or check the points on the sheet. Uncertainties the person did not state are not assumed.",
    }
