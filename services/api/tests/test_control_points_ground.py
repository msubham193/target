"""Control-point validation on the ground (CRS correctness hardening, Phase 2).

Residuals and tolerances are in metres, with the tolerance derived from the picture's own ground resolution:
sigma = sqrt((p*g)^2 + s_ref^2 + s_src^2), a 2-D residual is expected to be sqrt(2)*sigma, a warning is raised above twice
that and a failure above five times it. There is no universal pixel or metre threshold. Orientation is measured, not read
from axis names, so west/south, north/east and polar grids give no false positives. Everything is synthetic.
"""

import math

import numpy as np
import pyproj
import pytest

from geopoc.geo import controlpoints, orientation
from geopoc.geo.controlpoints import Point

WIDTH, HEIGHT = 400, 300
GRID = [(0, 0), (400, 0), (400, 300), (0, 300), (200, 150)]
SIX = [*GRID, (100, 50)]
SQRT2 = math.sqrt(2.0)


def affine_at(crs, x, y, pixel_m=10.0, rotation=0.0, stretch=1.0, shear_deg=0.0, flip=False):
    """A geotransform in `crs` for a picture laid square on the CRS's own grid (columns to the grid's right, rows
    down it), in true ground metres, optionally rotated, stretched, sheared or mirrored. Expressed in the CRS's own x
    and y, whichever way those point. For an east/north CRS that is a north-up picture; for EPSG:2046 (x west, y south)
    it is still a north-up picture; on a polar grid it is aligned with the grid, not with true north."""
    crs_obj = pyproj.CRS(crs)
    b = orientation.local_basis(crs_obj, np.array([float(x)]), np.array([float(y)]))
    ex, ey = b.ex[0], b.ey[0]
    right = orientation.grid_right(crs_obj, ex, ey)
    to_native = np.linalg.inv(np.column_stack([ex, ey]))  # east/north metres -> native units
    base = math.atan2(right[1], right[0])
    t = base + math.radians(rotation)
    column = pixel_m * np.array([math.cos(t), math.sin(t)])  # east/north metres per column step
    row_angle = t - math.radians(90 + shear_deg)
    row = pixel_m * stretch * np.array([math.cos(row_angle), math.sin(row_angle)])
    if flip:
        row = -row
    cx, cy = to_native @ column
    rx, ry = to_native @ row
    return [float(x), float(cx), float(rx), float(y), float(cy), float(ry)]


def at(gt, cells=GRID):
    return [
        Point(f"P{i + 1}", c, r, gt[0] + c * gt[1] + r * gt[2], gt[3] + c * gt[4] + r * gt[5])
        for i, (c, r) in enumerate(cells)
    ]


def fit(gt, crs="EPSG:32643", cells=GRID, move=None, **options):
    pts = at(gt, cells)
    for index, (dx, dy) in (move or {}).items():
        pts[index].x += dx
        pts[index].y += dy
    return controlpoints.fit(pts, WIDTH, HEIGHT, pyproj.CRS(crs), **options)


def status(result):
    return {c.code: c.status for c in result.checks}


UTM = affine_at("EPSG:32643", 500000, 3000000)


# --- the tolerance comes from the ground resolution, and says what was not stated ---------------------------------------------


def test_the_tolerance_follows_the_ground_resolution_not_a_fixed_number():
    coarse = fit(affine_at("EPSG:32643", 500000, 3000000, pixel_m=10.0))
    fine = fit(affine_at("EPSG:32643", 500000, 3000000, pixel_m=0.5))
    for result, g in ((coarse, 10.0), (fine, 0.5)):
        t = result.tolerance
        assert t["ground_resolution_m"] == pytest.approx(g, rel=1e-3)
        assert t["sigma_m"] == pytest.approx(g, rel=1e-3)  # one pixel to place a point; nothing else stated
        assert t["expected_residual_m"] == pytest.approx(SQRT2 * g, rel=1e-3)
        assert t["warn_above_m"] == pytest.approx(2 * SQRT2 * g, rel=1e-3)
        assert t["fail_above_m"] == pytest.approx(5 * SQRT2 * g, rel=1e-3)
    assert fine.tolerance["warn_above_m"] < coarse.tolerance["warn_above_m"] / 10


def test_a_ten_metre_pixel_warns_above_about_twenty_eight_metres_and_fails_above_about_seventy():
    t = fit(UTM).tolerance
    assert t["warn_above_m"] == pytest.approx(28.3, abs=0.2) and t["fail_above_m"] == pytest.approx(70.7, abs=0.5)


def test_uncertainties_that_were_not_stated_are_reported_as_not_stated_and_not_invented():
    t = fit(UTM).tolerance
    assert t["reference_uncertainty_stated"] is False and t["source_uncertainty_stated"] is False
    assert t["reference_uncertainty_m"] is None and t["source_uncertainty_m"] is None


def test_stated_uncertainties_widen_the_tolerance_in_quadrature():
    t = fit(UTM, reference_uncertainty_m=30.0, source_uncertainty_m=40.0).tolerance
    assert t["reference_uncertainty_stated"] and t["source_uncertainty_stated"]
    assert t["sigma_m"] == pytest.approx(math.sqrt(10.0**2 + 30.0**2 + 40.0**2), rel=1e-3)


def test_the_same_error_is_a_failure_for_a_fine_picture_and_unremarkable_for_a_coarse_one():
    move = {4: (60.0, 0.0)}  # one point 60 m off
    fine = fit(affine_at("EPSG:32643", 500000, 3000000, pixel_m=1.0), SIX and "EPSG:32643", cells=SIX, move=move)
    coarse = fit(affine_at("EPSG:32643", 500000, 3000000, pixel_m=40.0), "EPSG:32643", cells=SIX, move=move)
    assert status(fine)["CONTROL_POINTS_RESIDUALS"] == "failed" and fine.ok is False
    assert status(coarse)["CONTROL_POINTS_RESIDUALS"] == "ok" and coarse.ok


def test_residuals_are_reported_in_metres_with_rms_and_maximum():
    result = fit(UTM, cells=SIX, move={5: (0.0, 40.0)})
    assert result.max_m == pytest.approx(max(r["residual_m"] for r in result.residuals))
    assert result.rms_m == pytest.approx(math.sqrt(np.mean([r["residual_m"] ** 2 for r in result.residuals])))
    assert result.max_m > 20 and result.rms_m_dof == pytest.approx(
        math.sqrt(sum(r["residual_m"] ** 2 for r in result.residuals) / (len(SIX) - 3))
    )


# --- three points: an exact fit that cannot be checked ----------------------------------------------------------------------


def test_three_points_are_allowed_with_a_permanent_warning_and_no_leave_one_out():
    result = fit(UTM, cells=[(0, 0), (400, 0), (0, 300)])
    assert result.ok and result.redundant is False
    assert status(result)["EXACT_FIT_NO_CHECK"] == "warning"
    assert result.rms_m_dof is None  # nothing to compare: the zero residual means nothing
    assert result.leave_one_out["status"] == "unavailable" and "cannot be checked" in result.leave_one_out["reason"]
    assert all(r["leave_one_out_m"] is None for r in result.residuals)
    assert "CONTROL_POINTS_RESIDUALS" not in status(result)  # no "the residuals are fine" claim from a perfect fit


def test_four_or_more_points_do_not_carry_the_exact_fit_warning():
    assert "EXACT_FIT_NO_CHECK" not in status(fit(UTM, cells=GRID))


# --- leave-one-out ------------------------------------------------------------------------------------------------------------


def test_leave_one_out_finds_the_misplaced_point_and_matches_the_closed_form():
    result = fit(UTM, cells=SIX, move={5: (90.0, 0.0)})
    loo = result.leave_one_out
    assert loo["status"] == "complete" and loo["worst_label"] == "P6"
    assert status(result)["CONTROL_POINTS_LEAVE_ONE_OUT"] == "warning"
    # For linear least squares the held-out error is the ordinary residual over (1 - leverage). Check it independently.
    design = np.array([[1.0, p.column, p.row] for p in at(UTM, SIX)])
    pts = at(UTM, SIX)
    pts[5].x += 90.0
    targets = np.array([[p.x, p.y] for p in pts])
    hat = design @ np.linalg.inv(design.T @ design) @ design.T
    ordinary = targets - hat @ targets
    for i, row in enumerate(result.residuals):
        closed = np.hypot(*(ordinary[i] / (1.0 - hat[i, i])))
        assert row["leave_one_out_m"] == pytest.approx(closed, rel=2e-2)  # metres vs native units: ~10 m per unit


def test_removing_a_point_that_the_others_cannot_replace_is_reported_not_hidden():
    # Four points where three are collinear in the picture: take away the fourth and the rest cannot fix the sheet.
    cells = [(0, 0), (200, 0), (400, 0), (200, 300)]
    result = fit(UTM, cells=cells)
    assert result.ok
    assert result.leave_one_out["status"] == "partial" and "P4" in result.leave_one_out["essential"]
    assert status(result)["CONTROL_POINTS_LEAVE_ONE_OUT"] == "warning"
    assert next(r for r in result.residuals if r["label"] == "P4")["leave_one_out_m"] is None


def test_a_clean_fit_has_small_leave_one_out_errors():
    result = fit(UTM, cells=SIX)
    assert result.leave_one_out["status"] == "complete" and result.leave_one_out["max_m"] < 1e-3
    assert "CONTROL_POINTS_LEAVE_ONE_OUT" not in status(result)


# --- collinearity and duplicates -------------------------------------------------------------------------------------------------


def test_points_on_one_line_are_refused():
    result = fit(UTM, cells=[(0, 0), (100, 0), (200, 0), (300, 0)])
    assert result.ok is False and status(result)["CONTROL_POINTS_SPREAD"] == "failed"


def test_points_that_are_almost_on_a_line_are_warned_about():
    result = fit(UTM, cells=[(0, 0), (390, 4), (200, 2), (100, 1), (300, 5)])
    assert status(result)["CONTROL_POINTS_COLLINEAR"] == "warning"


def test_duplicate_points_are_refused_and_named():
    pts = at(UTM)
    pts[3].column, pts[3].row = pts[1].column, pts[1].row  # P4 on top of P2 in the picture
    result = controlpoints.fit(pts, WIDTH, HEIGHT, pyproj.CRS("EPSG:32643"))
    assert result.ok is False
    [distinct] = [c for c in result.checks if c.code == "CONTROL_POINTS_DISTINCT"]
    assert distinct.status == "failed" and "P2" in distinct.message and "P4" in distinct.message
    pts = at(UTM)
    pts[2].x, pts[2].y = pts[0].x, pts[0].y  # the same ground coordinate twice
    assert controlpoints.fit(pts, WIDTH, HEIGHT, pyproj.CRS("EPSG:32643")).ok is False


# --- anisotropy, rotation, shear, mirroring: measured on the ground --------------------------------------------------------------


def test_anisotropic_pixels_are_measured_on_the_ground():
    stretched = fit(affine_at("EPSG:32643", 500000, 3000000, stretch=1.15))
    assert (
        stretched.scale_ratio == pytest.approx(1.15, rel=1e-3)
        and status(stretched)["CONTROL_POINTS_SCALE"] == "warning"
    )
    assert stretched.ground_pixel_m == pytest.approx([10.0, 11.5], rel=1e-3)
    very = fit(affine_at("EPSG:32643", 500000, 3000000, stretch=1.4))
    assert very.ok is False and status(very)["CONTROL_POINTS_SCALE"] == "failed"
    assert "CONTROL_POINTS_SCALE" not in status(fit(UTM))  # square pixels: nothing said


def test_a_rotated_scan_is_reported_for_information_and_still_applied():
    result = fit(affine_at("EPSG:32643", 500000, 3000000, rotation=12.0))
    assert result.ok and result.rotation_degrees == pytest.approx(12.0, abs=1e-5)
    assert status(result)["CONTROL_POINTS_ROTATION"] == "warning" and result.shear_degrees == pytest.approx(0, abs=1e-6)
    assert "CONTROL_POINTS_ROTATION" not in status(fit(affine_at("EPSG:32643", 500000, 3000000, rotation=3.0)))


def test_shear_is_not_confused_with_rotation_and_is_judged_by_its_effect_on_the_sheet():
    sheared = fit(affine_at("EPSG:32643", 500000, 3000000, shear_deg=2.0))
    assert sheared.shear_degrees == pytest.approx(2.0, abs=1e-4)
    assert sheared.shear_effect_m == pytest.approx(400 * 10.0 * math.tan(math.radians(2.0)), rel=1e-2)
    assert status(sheared)["CONTROL_POINTS_SHEAR"] in ("warning", "failed")
    assert fit(affine_at("EPSG:32643", 500000, 3000000, rotation=30.0)).shear_degrees == pytest.approx(0, abs=1e-5)
    tiny = fit(affine_at("EPSG:32643", 500000, 3000000, shear_deg=0.01))
    assert "CONTROL_POINTS_SHEAR" not in status(tiny)  # a hundredth of a degree moves the far edge a few centimetres


def test_a_real_mirror_is_flagged_and_a_north_up_picture_is_not():
    assert status(fit(affine_at("EPSG:32643", 500000, 3000000, flip=True)))["CONTROL_POINTS_ORIENTATION"] == "warning"
    assert "CONTROL_POINTS_ORIENTATION" not in status(fit(UTM))


# --- other CRSs: units, degrees, and axes that are not east/north ------------------------------------------------------------------


def test_a_geographic_crs_is_judged_in_metres_not_degrees():
    gt = affine_at("EPSG:4326", 77.0, 28.1, pixel_m=10.0)
    result = fit(gt, "EPSG:4326")
    assert result.ok and result.ground_resolution_m == pytest.approx(10.0, rel=1e-3)
    assert result.scale_ratio == pytest.approx(1.0, abs=1e-3)  # unequal degrees per pixel, equal metres
    assert abs(gt[1]) != pytest.approx(abs(gt[5]), rel=1e-2)
    assert result.tolerance["sigma_m"] == pytest.approx(10.0, rel=1e-3)


def test_a_projected_crs_in_feet_is_judged_in_metres():
    # EPSG:2272 is a state plane CRS in US survey feet. A 10 m pixel is 32.8 ft.
    gt = affine_at("EPSG:2272", 2700000.0, 300000.0, pixel_m=10.0)
    result = fit(gt, "EPSG:2272")
    assert abs(gt[1]) == pytest.approx(10.0 / 0.3048006096, rel=1e-3)
    assert result.ground_resolution_m == pytest.approx(10.0, rel=1e-3) and result.ok
    moved = fit(gt, "EPSG:2272", cells=SIX, move={5: (300.0, 0.0)})  # one point 300 ft = 91.4 m off
    # Held out, that point is put exactly where it belongs, so its leave-one-out error is the displacement itself.
    assert moved.leave_one_out["worst_m"] == pytest.approx(300.0 * 0.3048006096, abs=0.05)
    assert moved.leave_one_out["worst_label"] == "P6" and status(moved)["CONTROL_POINTS_LEAVE_ONE_OUT"] == "warning"


@pytest.mark.parametrize(
    ("crs", "x", "y"),
    [
        ("EPSG:2046", 50000.0, -3750000.0),  # Hartebeesthoek94 / Lo15: x points west, y points south
        ("EPSG:2193", 1748000.0, 5428000.0),  # NZTM2000: northing is listed first
        ("EPSG:3031", 100000.0, -200000.0),  # Antarctic polar stereographic: grid directions
        ("EPSG:3031", 1000000.0, 1000000.0),
    ],
)
def test_north_up_pictures_in_unusual_axis_conventions_raise_no_false_alarm(crs, x, y):
    result = fit(affine_at(crs, x, y), crs)
    assert result.ok and result.handedness == "normal"
    assert result.shear_degrees == pytest.approx(0, abs=1e-4) and result.scale_ratio == pytest.approx(1.0, abs=1e-3)
    assert result.rotation_degrees == pytest.approx(0.0, abs=1e-4)
    assert not {
        "CONTROL_POINTS_ORIENTATION",
        "CONTROL_POINTS_ROTATION",
        "CONTROL_POINTS_SHEAR",
        "CONTROL_POINTS_SCALE",
    } & ({code for code, state in status(result).items() if state != "ok"})


@pytest.mark.parametrize(("crs", "x", "y"), [("EPSG:2046", 50000.0, -3750000.0), ("EPSG:2193", 1748000.0, 5428000.0)])
def test_a_true_mirror_is_still_found_in_those_conventions(crs, x, y):
    assert status(fit(affine_at(crs, x, y, flip=True), crs))["CONTROL_POINTS_ORIENTATION"] == "warning"


def test_a_rotation_is_found_in_those_conventions_too():
    for crs, x, y in (
        ("EPSG:2046", 50000.0, -3750000.0),
        ("EPSG:2193", 1748000.0, 5428000.0),
        ("EPSG:3031", 100000.0, -200000.0),
    ):
        assert fit(affine_at(crs, x, y, rotation=20.0), crs).rotation_degrees == pytest.approx(20.0, abs=1e-3)


def test_coordinates_outside_the_crs_domain_fail_clearly():
    pts = at(UTM)
    pts[0].x = 1e12
    result = controlpoints.fit(pts, WIDTH, HEIGHT, pyproj.CRS("EPSG:32643"))
    assert result.ok is False and status(result)["CONTROL_POINTS_DOMAIN"] == "failed"


# --- uncertainty at the corners and what is stored --------------------------------------------------------------------------------


def test_corner_uncertainty_is_largest_where_the_points_do_not_reach():
    spread = fit(UTM, cells=SIX)
    clustered = fit(UTM, cells=[(150, 100), (250, 100), (250, 200), (150, 200)])
    assert clustered.corner_uncertainty_m > 3 * spread.corner_uncertainty_m
    assert {c["corner"] for c in spread.corners} == {"top-left", "top-right", "bottom-right", "bottom-left"}


def test_the_stored_evidence_is_schema_two_and_carries_the_ground_assessment():
    result = fit(UTM, cells=SIX, move={5: (25.0, 0.0)}, source_uncertainty_m=15.0)
    evidence = controlpoints.evidence(result, crs_id="EPSG:32643", source="Printed ticks", software="test")
    assert evidence["evidence_schema"] == 2 and evidence["exact_fit"] is False
    assert evidence["rms_m"] == result.rms_m and evidence["max_m"] == result.max_m
    assert (
        evidence["tolerance"]["source_uncertainty_stated"] is True
        and evidence["tolerance"]["reference_uncertainty_stated"] is False
    )
    assert evidence["ground"]["handedness"] == "normal" and evidence["ground"]["resolution_m"] == pytest.approx(
        10.0, rel=1e-3
    )
    assert evidence["leave_one_out"]["status"] == "complete" and "per_point" not in evidence["leave_one_out"]
    assert evidence["corner_uncertainty_m"] > 0 and len(evidence["corners"]) == 4
    assert "thresholds" not in evidence  # the fixed pixel thresholds of schema 1 no longer exist
