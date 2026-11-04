"""A repeatable check of georeferenced map sheets against independent reference points.

Run it on authorised real sheets (Open Series Map, Everest Series Map) once they are available, and it says how far each
sheet's georeferencing is from points that were not used to make it. Run it on nothing and it says so. It is built so
that it cannot overstate what was checked:

* It only reports **VALIDATED** for a sheet declared `real`, with a named authorisation, whose reference points pass the
  tolerance. A `synthetic` sheet is only ever reported as `SYNTHETIC (self-test only)`.
* With no real sheet in the manifest the overall result is **NOT VALIDATED**, whatever else passed.
* A CRS is given as an authority code (`EPSG:...`) and nothing else. A name, a series label or a file name never selects
  one, and no datum, ellipsoid, projection or transformation parameter is guessed, so a sheet whose CRS is not in the
  local registry cannot be checked here and is reported as such.
* It reads files, never writes them (output goes only to the paths given), never opens the database, never reads `.env`,
  never imports a network client and sends nothing anywhere. Source data stays on this machine.
* A datum shift worse than 10 m, or with no stated accuracy, is not acknowledged on anybody's behalf: it is reported as
  needing acknowledgement and the sheet is not evaluated through it.

Usage:  python -m geopoc.validation.mapsheets --manifest MANIFEST.json --json OUT.json --markdown OUT.md
Exit status: 0 every real sheet validated; 1 a real sheet failed or the manifest is invalid; 3 NOT VALIDATED (no real
sheet).
"""

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyproj
import rasterio
from affine import Affine

from geopoc.geo import controlpoints, orientation
from geopoc.geo import transform as transforms
from geopoc.geo.controlpoints import Point
from geopoc.ingestion.raster import footprint_checked

MANIFEST_VERSION = 1
AUTHORITY_CODE = re.compile(r"^[A-Z][A-Z0-9_]*:\d+$")  # EPSG:32643, ESRI:102100 ... and nothing that is a name
# A reference point further than this from where the sheet puts it is a failure, unless the manifest says otherwise.
DEFAULT_TOLERANCE_M = 25.0
EXIT_VALIDATED, EXIT_FAILED, EXIT_NOT_VALIDATED = 0, 1, 3
PROVENANCE = ("real", "synthetic")
NOT_VALIDATED = "NOT VALIDATED"
SELF_TEST = "SYNTHETIC (self-test only)"


class ManifestError(ValueError):
    """The manifest cannot be used. Nothing is evaluated from a manifest that is wrong."""


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestError(f"{where} must be a finite number.")
    return float(value)


def _points(raw: Any, where: str, *, need_uncertainty: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ManifestError(f"{where} must be a list of points.")
    out = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"{where}[{index}] must be an object.")
        point = {
            "label": str(item.get("label") or f"#{index + 1}"),
            **{k: _number(item.get(k), f"{where}[{index}].{k}") for k in ("column", "row", "x", "y")},
        }
        if "uncertainty_m" in item and item["uncertainty_m"] is not None:
            point["uncertainty_m"] = _number(item["uncertainty_m"], f"{where}[{index}].uncertainty_m")
        elif need_uncertainty:
            raise ManifestError(
                f"{where}[{index}] needs uncertainty_m: say how well that reference coordinate is known. It is not "
                "assumed."
            )
        out.append(point)
    return out


def load_manifest(path: Path) -> dict[str, Any]:
    """Read and check a manifest. Relative file paths are relative to the manifest."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"The manifest could not be read: {exc}") from exc
    if not isinstance(data, dict) or data.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestError(f"The manifest must be a JSON object with manifest_version {MANIFEST_VERSION}.")
    sheets = data.get("sheets")
    if not isinstance(sheets, list):
        raise ManifestError("The manifest needs a 'sheets' list (it may be empty).")
    seen: set[str] = set()
    checked = []
    for index, sheet in enumerate(sheets):
        where = f"sheets[{index}]"
        if not isinstance(sheet, dict):
            raise ManifestError(f"{where} must be an object.")
        sheet_id = str(sheet.get("id") or "").strip()
        if not sheet_id or sheet_id in seen:
            raise ManifestError(f"{where} needs a unique non-empty id.")
        seen.add(sheet_id)
        provenance = sheet.get("provenance")
        if provenance not in PROVENANCE:
            raise ManifestError(f"{where} ({sheet_id}) needs provenance 'real' or 'synthetic'.")
        authorisation = str(sheet.get("authorisation") or "").strip()
        if provenance == "real" and not authorisation:
            raise ManifestError(
                f"{where} ({sheet_id}) is declared real but names no authorisation. Say who authorised using this "
                "sample."
            )
        crs = str(sheet.get("declared_crs") or "").strip()
        if not AUTHORITY_CODE.match(crs):
            raise ManifestError(
                f"{where} ({sheet_id}) needs declared_crs as an authority code such as EPSG:32643. A name, a series "
                "label "
                "or a file name never selects a CRS."
            )
        if not sheet.get("file"):
            raise ManifestError(f"{where} ({sheet_id}) needs a file.")
        entry = {
            "id": sheet_id,
            "provenance": provenance,
            "authorisation": authorisation or None,
            "series": sheet.get("series"),
            "file": str((path.parent / str(sheet["file"])).resolve()),
            "declared_crs": crs,
            "reference_points": _points(
                sheet.get("reference_points", []), f"{where}.reference_points", need_uncertainty=True
            ),
            "control_points": _points(
                sheet.get("control_points", []), f"{where}.control_points", need_uncertainty=False
            ),
            "tolerance_m": _number(sheet.get("tolerance_m", DEFAULT_TOLERANCE_M), f"{where}.tolerance_m"),
        }
        if not entry["reference_points"]:
            raise ManifestError(
                f"{where} ({sheet_id}) needs reference_points: independent points that were not used to georeference "
                "it."
            )
        checked.append(entry)
    return {"sheets": checked}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _distance_m(crs: pyproj.CRS, x0: float, y0: float, x1: float, y1: float) -> float | None:
    """Ground distance between two native points on the CRS's own ellipsoid (no datum shift is involved)."""
    basis = orientation.local_basis(crs, np.array([(x0 + x1) / 2]), np.array([(y0 + y1) / 2]))
    if basis is None:
        return None
    return float(np.hypot(*basis.to_metres(np.array([x1 - x0]), np.array([y1 - y0]))[0]))


def _score(crs: pyproj.CRS, geotransform: list[float], points: list[dict[str, Any]]) -> dict[str, Any]:
    affine = Affine.from_gdal(*geotransform)
    rows, errors = [], []
    for point in points:
        x, y = affine * (point["column"] + 0.0, point["row"] + 0.0)
        error = _distance_m(crs, x, y, point["x"], point["y"])
        rows.append({"label": point["label"], "error_m": error, "reference_uncertainty_m": point.get("uncertainty_m")})
        if error is not None:
            errors.append(error)
    return {
        "points": rows,
        "rms_m": float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
        "max_m": float(max(errors)) if errors else None,
        "evaluated": len(errors),
        "not_evaluable": len(rows) - len(errors),
    }


def evaluate_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    """One sheet's report. Never raises for a bad sheet: the reason is the result."""
    report: dict[str, Any] = {
        "id": sheet["id"],
        "provenance": sheet["provenance"],
        "authorisation": sheet["authorisation"],
        "series": sheet["series"],
        "declared_crs": sheet["declared_crs"],
        "findings": [],
        "verdict": None,
    }
    path = Path(sheet["file"])
    if not path.is_file():
        report["verdict"] = "FAILED"
        report["findings"].append(f"The file {path.name} does not exist.")
        return report
    report["sha256"] = _sha256(path)
    try:
        crs = pyproj.CRS.from_user_input(sheet["declared_crs"])
    except pyproj.exceptions.CRSError:
        report["verdict"] = "CANNOT BE CHECKED"
        report["findings"].append(
            f"{sheet['declared_crs']} is not in the local PROJ registry. No definition is guessed or substituted, so "
            "this "
            "sheet cannot be checked here."
        )
        return report
    if not (crs.is_geographic or crs.is_projected):
        report["verdict"] = "CANNOT BE CHECKED"
        report["findings"].append("The declared CRS is not a geographic or projected CRS.")
        return report

    with rasterio.open(path) as src:
        width, height = src.width, src.height
        file_gt = list(src.transform.to_gdal()) if src.transform != Affine.identity() else None
        file_crs = src.crs.to_wkt() if src.crs else None
    report["raster"] = {
        "width": width,
        "height": height,
        "has_geotransform": file_gt is not None,
        "has_crs": file_crs is not None,
    }

    # The datum shift to longitude/latitude: what the sheet's CRS would need, and whether it may be used unacknowledged.
    plan = transforms.plan(crs, "EPSG:4326")
    operation = plan.usable[0] if plan.usable and plan.status != "selection_required" else None
    report["transformation"] = {
        "status": plan.status,
        "operation_id": operation.id if operation else None,
        "classification": operation.klass if operation else None,
        "accuracy_m": operation.accuracy_m if operation else None,
        "requires_acknowledgement": operation.requires_acknowledgement if operation else None,
        "note": "Only the sheet's own georeferencing is scored below, in its own CRS. Moving it to WGS 84 is not done "
        "here.",
    }
    if plan.status == "unavailable":
        report["findings"].append(
            "No usable datum transformation to WGS 84 exists for this CRS (ballpark only, or a grid is missing)."
        )
    elif operation is not None and operation.requires_acknowledgement:
        report["findings"].append(
            f"Moving this CRS to WGS 84 needs an acknowledgement ({operation.id}, accuracy "
            f"{'not stated' if operation.accuracy_m is None else f'about {operation.accuracy_m:g} m'}). None was "
            "given on anyone's behalf."
        )

    # Footprint: does the sheet's own georeferencing land anywhere on the earth through an allowed operation?
    geotransform = file_gt
    source = "the file's own georeferencing"
    if sheet["control_points"]:
        fit = controlpoints.fit(
            [Point(p["label"], p["column"], p["row"], p["x"], p["y"]) for p in sheet["control_points"]],
            width,
            height,
            crs,
        )
        report["control_point_fit"] = {
            "ok": fit.ok,
            "rms_m": fit.rms_m,
            "max_m": fit.max_m,
            "exact_fit": not fit.redundant,
            "ground_resolution_m": fit.ground_resolution_m,
            "leave_one_out": {k: v for k, v in (fit.leave_one_out or {}).items() if k != "per_point"},
            "corner_uncertainty_m": fit.corner_uncertainty_m,
            "checks": [{"code": c.code, "status": c.status} for c in fit.checks],
        }
        if not fit.geotransform or not fit.ok:
            report["verdict"] = "FAILED"
            report["findings"].append("The control points did not produce an acceptable fit.")
            return report
        geotransform, source = fit.geotransform, "a fit to the control points in the manifest"
        if not fit.redundant:
            report["findings"].append("The fit uses exactly three control points, so it cannot reveal a wrong point.")
    if geotransform is None:
        report["verdict"] = "FAILED"
        report["findings"].append("The sheet has no geotransform and the manifest gives no control points.")
        return report
    report["georeferencing_from"] = source

    checked = footprint_checked(Affine.from_gdal(*geotransform), width, height, crs)
    report["footprint"] = {
        "placed": checked.polygon is not None,
        "crosses_antimeridian": checked.crosses_antimeridian,
        "blocked_by": checked.error.code if checked.error else None,
        "domain_failure": checked.domain_failure,
    }
    if checked.error is not None:
        report["findings"].append(f"{checked.error.title}. The footprint is not computed through a refused operation.")

    scored = _score(crs, geotransform, sheet["reference_points"])
    report["reference_points"] = scored
    tolerance = sheet["tolerance_m"]
    report["tolerance_m"] = tolerance
    if scored["not_evaluable"]:
        report["findings"].append(
            f"{scored['not_evaluable']} reference point(s) are outside the CRS's domain and were not scored."
        )
    if scored["evaluated"] == 0:
        report["verdict"] = "FAILED"
        report["findings"].append("No reference point could be scored.")
    elif scored["max_m"] > tolerance:
        report["verdict"] = "FAILED"
        report["findings"].append(
            f"The worst reference point is {scored['max_m']:.1f} m from where {source} puts it, beyond the "
            f"{tolerance:g} m tolerance."
        )
    else:
        report["verdict"] = "PASSED"
        report["findings"].append(
            f"Every scored reference point is within {tolerance:g} m of where {source} puts it (worst "
            f"{scored['max_m']:.1f} m)."
        )
    if sheet["provenance"] == "synthetic":
        report["verdict_label"] = SELF_TEST
    else:
        report["verdict_label"] = "VALIDATED" if report["verdict"] == "PASSED" else report["verdict"]
    return report


def run(manifest_path: Path) -> dict[str, Any]:
    """Evaluate every sheet in a manifest and decide the overall result."""
    manifest = load_manifest(manifest_path)
    sheets = [evaluate_sheet(s) for s in manifest["sheets"]]
    real = [s for s in sheets if s["provenance"] == "real"]
    if not real:
        overall, exit_code = NOT_VALIDATED, EXIT_NOT_VALIDATED
        why = (
            "The manifest contains no real, authorised sample sheet. Synthetic sheets only check that this harness "
            "works; "
            "they say nothing about real Open Series Map or Everest Series Map sheets."
        )
    elif all(s.get("verdict_label") == "VALIDATED" for s in real):
        overall, exit_code, why = (
            "VALIDATED",
            EXIT_VALIDATED,
            f"{len(real)} real sheet(s) passed against their reference points.",
        )
    else:
        overall, exit_code = "FAILED", EXIT_FAILED
        why = (
            "At least one real sheet did not pass: "
            + ", ".join(s["id"] for s in real if s.get("verdict_label") != "VALIDATED")
            + "."
        )
    return {
        "harness": "geopoc.validation.mapsheets",
        "manifest_version": MANIFEST_VERSION,
        "overall": overall,
        "why": why,
        "exit_code": exit_code,
        "real_sheets": len(real),
        "synthetic_sheets": len(sheets) - len(real),
        "versions": {
            "proj": pyproj.proj_version_str,
            "pyproj": pyproj.__version__,
            "rasterio": rasterio.__version__,
            "network": "disabled",
        },
        "sheets": sheets,
    }


def to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Map sheet validation",
        "",
        f"**Overall: {result['overall']}.** {result['why']}",
        "",
        f"Real sheets: {result['real_sheets']}. Synthetic self-test sheets: {result['synthetic_sheets']}. "
        f"PROJ {result['versions']['proj']}, network disabled. Nothing was written to the database and nothing was "
        "transmitted.",
        "",
    ]
    for sheet in result["sheets"]:
        label = sheet.get("verdict_label") or sheet["verdict"]
        lines += [f"## {sheet['id']} ({sheet['provenance']}): {label}", "", f"- Declared CRS: {sheet['declared_crs']}"]
        if sheet.get("series"):
            lines.append(f"- Series: {sheet['series']} (a label only; it never selected the CRS)")
        if sheet.get("authorisation"):
            lines.append(f"- Authorisation: {sheet['authorisation']}")
        if sheet.get("sha256"):
            lines.append(f"- SHA-256: {sheet['sha256']}")
        scored = sheet.get("reference_points")
        if scored and scored["rms_m"] is not None:
            lines.append(
                f"- Reference points: {scored['evaluated']} scored, RMS {scored['rms_m']:.2f} m, worst "
                f"{scored['max_m']:.2f} m "
                f"(tolerance {sheet['tolerance_m']:g} m)"
            )
        lines += [f"- {finding}" for finding in sheet["findings"]]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline check of georeferenced map sheets against reference points.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--json", type=Path, help="write the full result here")
    parser.add_argument("--markdown", type=Path, help="write a readable report here")
    args = parser.parse_args(argv)
    try:
        result = run(args.manifest)
    except ManifestError as exc:
        print(f"MANIFEST INVALID: {exc}", file=sys.stderr)
        return EXIT_FAILED
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    report = to_markdown(result)
    if args.markdown:
        args.markdown.write_text(report + "\n", encoding="utf-8")
    print(f"{result['overall']}: {result['why']}")
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
