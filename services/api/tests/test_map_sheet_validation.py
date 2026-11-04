"""The offline map-sheet validation harness (geopoc.validation.mapsheets).

It must be able to say NOT VALIDATED, must never call a synthetic sheet validated, must never guess a CRS, and must stay
offline and away from the database. The sheets here are synthetic, built by the tests; where one is declared `real` it is
only to exercise the harness's own logic (the label is what the test is about), not evidence about any real map.
"""

import json
import socket
from pathlib import Path

import pytest
from affine import Affine

from geopoc.validation import mapsheets
from tests import media

UTM = "EPSG:32643"
GT = Affine(10, 0, 500000, 0, -10, 3000000)


def write_sheet(folder: Path, name="sheet.tif", crs=UTM, transform=GT, size=(400, 300)) -> Path:
    path = folder / name
    path.write_bytes(media.geotiff_bytes(crs=crs, transform=transform, size=size))
    return path


def refs(shift=(0.0, 0.0), uncertainty=5.0):
    """Four reference points at known pixels, placed where GT puts them plus `shift` metres."""
    cells = [(0, 0), (400, 0), (400, 300), (0, 300)]
    return [
        {
            "label": f"R{i + 1}",
            "column": c,
            "row": r,
            "x": 500000 + 10 * c + shift[0],
            "y": 3000000 - 10 * r + shift[1],
            "uncertainty_m": uncertainty,
        }
        for i, (c, r) in enumerate(cells)
    ]


def manifest(folder: Path, *sheets) -> Path:
    path = folder / "manifest.json"
    path.write_text(json.dumps({"manifest_version": 1, "sheets": list(sheets)}), encoding="utf-8")
    return path


def entry(sheet_id="s1", provenance="synthetic", file="sheet.tif", **extra):
    base = {"id": sheet_id, "provenance": provenance, "file": file, "declared_crs": UTM, "reference_points": refs()}
    if provenance == "real":
        base["authorisation"] = "Test authorisation for exercising the harness"
    return {**base, **extra}


# --- it can say NOT VALIDATED, and never overstates ---------------------------------------------------------------------------------


def test_no_sheets_at_all_is_not_validated(tmp_path):
    result = mapsheets.run(manifest(tmp_path))
    assert result["overall"] == "NOT VALIDATED" and result["exit_code"] == 3 and result["real_sheets"] == 0
    assert "no real, authorised sample" in result["why"]


def test_synthetic_sheets_pass_their_self_test_but_are_never_reported_as_validated(tmp_path):
    write_sheet(tmp_path)
    result = mapsheets.run(manifest(tmp_path, entry()))
    [sheet] = result["sheets"]
    assert sheet["verdict"] == "PASSED" and sheet["verdict_label"] == "SYNTHETIC (self-test only)"
    assert (
        result["overall"] == "NOT VALIDATED" and result["exit_code"] == 3
    )  # passing synthetic sheets prove nothing about real ones
    assert "VALIDATED" not in sheet["verdict_label"].replace("NOT VALIDATED", "")


def test_a_real_sheet_that_agrees_with_its_reference_points_is_validated(tmp_path):
    write_sheet(tmp_path)
    result = mapsheets.run(manifest(tmp_path, entry(provenance="real")))
    [sheet] = result["sheets"]
    assert sheet["verdict_label"] == "VALIDATED" and result["overall"] == "VALIDATED" and result["exit_code"] == 0
    assert sheet["reference_points"]["max_m"] < 1.0 and len(sheet["sha256"]) == 64


def test_a_real_sheet_that_is_off_fails_with_how_far(tmp_path):
    write_sheet(tmp_path)
    off = entry(provenance="real", reference_points=refs(shift=(60.0, 0.0)), tolerance_m=25.0)
    result = mapsheets.run(manifest(tmp_path, off))
    [sheet] = result["sheets"]
    assert sheet["verdict"] == "FAILED" and result["overall"] == "FAILED" and result["exit_code"] == 1
    assert any("60." in finding or "59." in finding for finding in sheet["findings"])


def test_one_failing_real_sheet_fails_the_run_even_if_another_passes(tmp_path):
    write_sheet(tmp_path)
    good = entry("good", provenance="real")
    bad = entry("bad", provenance="real", reference_points=refs(shift=(0.0, 200.0)))
    result = mapsheets.run(manifest(tmp_path, good, bad))
    assert result["overall"] == "FAILED" and "bad" in result["why"] and "good" not in result["why"]


# --- control points ------------------------------------------------------------------------------------------------------------------


def test_a_sheet_without_georeferencing_is_scored_from_control_points_that_are_not_the_reference_points(tmp_path):
    path = tmp_path / "scan.tif"
    path.write_bytes(media.geotiff_bytes(crs=None, transform=Affine.identity(), size=(400, 300)))
    control = [{**p, "label": f"C{i}"} for i, p in enumerate(refs(), 1)] + [
        {"label": "C5", "column": 200, "row": 150, "x": 502000.0, "y": 2998500.0}
    ]
    result = mapsheets.run(manifest(tmp_path, entry(file="scan.tif", control_points=control)))
    [sheet] = result["sheets"]
    assert sheet["georeferencing_from"] == "a fit to the control points in the manifest"
    assert sheet["control_point_fit"]["ok"] is True and sheet["control_point_fit"]["exact_fit"] is False
    assert sheet["verdict"] == "PASSED"


def test_a_sheet_with_neither_georeferencing_nor_control_points_cannot_pass(tmp_path):
    (tmp_path / "scan.tif").write_bytes(media.geotiff_bytes(crs=None, transform=Affine.identity(), size=(400, 300)))
    [sheet] = mapsheets.run(manifest(tmp_path, entry(file="scan.tif")))["sheets"]
    assert sheet["verdict"] == "FAILED" and "no geotransform" in " ".join(sheet["findings"])


# --- nothing is guessed --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("crs", ["Everest 1830", "ESM", "Open Series Map", "Everest:1830", "epsg:32643", ""])
def test_a_crs_must_be_an_authority_code_and_a_name_never_selects_one(tmp_path, crs):
    write_sheet(tmp_path)
    with pytest.raises(mapsheets.ManifestError, match="authority code"):
        mapsheets.run(manifest(tmp_path, entry(declared_crs=crs)))


def test_a_code_the_local_registry_does_not_have_is_reported_not_substituted(tmp_path):
    write_sheet(tmp_path)
    [sheet] = mapsheets.run(manifest(tmp_path, entry(declared_crs="EPSG:99999")))["sheets"]
    assert sheet["verdict"] == "CANNOT BE CHECKED" and "No definition is guessed" in sheet["findings"][0]


def test_a_series_label_is_kept_as_a_label_and_changes_nothing(tmp_path):
    write_sheet(tmp_path)
    plain = mapsheets.run(manifest(tmp_path, entry()))["sheets"][0]
    labelled = mapsheets.run(manifest(tmp_path, entry(series="Everest Series Map")))["sheets"][0]
    assert labelled["series"] == "Everest Series Map"
    assert (
        labelled["reference_points"] == plain["reference_points"] and labelled["declared_crs"] == plain["declared_crs"]
    )


def test_a_datum_shift_needing_acknowledgement_is_reported_and_not_acknowledged_for_anyone(tmp_path):
    write_sheet(tmp_path, crs="EPSG:24313", transform=Affine(10, 0, 500000, 0, -10, 3170000))
    sheet = entry(declared_crs="EPSG:24313")
    sheet["reference_points"] = [{**p, "y": p["y"] + 170000} for p in refs()]
    [report] = mapsheets.run(manifest(tmp_path, sheet))["sheets"]
    assert (
        report["transformation"]["requires_acknowledgement"] is True
        and report["transformation"]["operation_id"] == "EPSG:1247"
    )
    assert (
        report["footprint"]["blocked_by"] == "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"
        and report["footprint"]["placed"] is False
    )
    assert any("needs an acknowledgement" in finding for finding in report["findings"])
    assert report["verdict"] == "PASSED"  # the sheet's own CRS positions are still scored; WGS 84 is not claimed


def test_a_ballpark_only_crs_is_reported_as_having_no_usable_transformation(tmp_path):
    write_sheet(tmp_path, crs="EPSG:24372", transform=Affine(10, 0, 1000000, 0, -10, 1000000))
    sheet = entry(declared_crs="EPSG:24372")
    sheet["reference_points"] = [{**p, "x": p["x"] + 500000 - 500000, "y": p["y"] - 2000000} for p in refs()]
    [report] = mapsheets.run(manifest(tmp_path, sheet))["sheets"]
    assert report["transformation"]["status"] == "unavailable" and report["transformation"]["operation_id"] is None


# --- the manifest ----------------------------------------------------------------------------------------------------------------------


def test_a_real_sheet_must_name_who_authorised_its_use(tmp_path):
    write_sheet(tmp_path)
    bad = entry(provenance="real")
    del bad["authorisation"]
    with pytest.raises(mapsheets.ManifestError, match="authorisation"):
        mapsheets.run(manifest(tmp_path, bad))


def test_reference_points_must_state_how_well_they_are_known_and_that_is_never_assumed(tmp_path):
    write_sheet(tmp_path)
    bad = entry()
    bad["reference_points"] = [{k: v for k, v in p.items() if k != "uncertainty_m"} for p in refs()]
    with pytest.raises(mapsheets.ManifestError, match="uncertainty_m"):
        mapsheets.run(manifest(tmp_path, bad))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(provenance="maybe"),
        lambda e: e.pop("file"),
        lambda e: e.update(reference_points=[]),
        lambda e: e["reference_points"][0].update(x=float("nan")),
        lambda e: e["reference_points"][0].update(column="3"),
    ],
)
def test_an_invalid_manifest_is_refused_before_anything_is_evaluated(tmp_path, mutate):
    write_sheet(tmp_path)
    bad = entry()
    mutate(bad)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"manifest_version": 1, "sheets": [bad]}, allow_nan=True), encoding="utf-8")
    with pytest.raises(mapsheets.ManifestError):
        mapsheets.run(path)


def test_duplicate_ids_and_a_wrong_version_are_refused(tmp_path):
    write_sheet(tmp_path)
    with pytest.raises(mapsheets.ManifestError, match="unique"):
        mapsheets.run(manifest(tmp_path, entry(), entry()))
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"manifest_version": 0, "sheets": []}), encoding="utf-8")
    with pytest.raises(mapsheets.ManifestError, match="manifest_version"):
        mapsheets.run(path)


def test_a_missing_file_is_a_failure_with_the_reason(tmp_path):
    [sheet] = mapsheets.run(manifest(tmp_path, entry(file="absent.tif")))["sheets"]
    assert sheet["verdict"] == "FAILED" and "does not exist" in sheet["findings"][0]


# --- it stays offline, read-only and away from the database -------------------------------------------------------------------


def test_the_run_makes_no_network_connection_and_never_touches_the_database(tmp_path, monkeypatch):
    write_sheet(tmp_path)

    def no_network(*_args, **_kwargs):
        raise AssertionError("the harness tried to open a network connection")

    def no_database(*_args, **_kwargs):
        raise AssertionError("the harness tried to open the database")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    import geopoc.db.session as session

    monkeypatch.setattr(session, "get_engine", no_database)
    import geopoc.config as config

    monkeypatch.setattr(config, "get_settings", no_database)  # the settings file (.env) is never read either
    result = mapsheets.run(manifest(tmp_path, entry()))
    assert result["versions"]["network"] == "disabled" and result["sheets"][0]["verdict"] == "PASSED"


def test_the_harness_module_imports_no_network_client_and_no_database_session():
    source = Path(mapsheets.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import httpx",
        "import requests",
        "import urllib",
        "import socket",
        "geopoc.db",
        "get_settings",
        "sessionmaker",
    ):
        assert forbidden not in source, forbidden


def test_the_original_file_is_not_modified(tmp_path):
    path = write_sheet(tmp_path)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    mapsheets.run(manifest(tmp_path, entry()))
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "manifest.json",
        "sheet.tif",
    ]  # no sidecar or .aux.xml appeared


# --- the command line ----------------------------------------------------------------------------------------------------------------


def test_the_command_writes_both_reports_and_returns_the_exit_status(tmp_path, capsys):
    write_sheet(tmp_path)
    path = manifest(tmp_path, entry())
    code = mapsheets.main(
        ["--manifest", str(path), "--json", str(tmp_path / "out.json"), "--markdown", str(tmp_path / "out.md")]
    )
    assert code == 3
    assert "NOT VALIDATED" in capsys.readouterr().out
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data["overall"] == "NOT VALIDATED" and data["sheets"][0]["verdict_label"] == "SYNTHETIC (self-test only)"
    text = (tmp_path / "out.md").read_text(encoding="utf-8")
    assert "**Overall: NOT VALIDATED.**" in text and "SYNTHETIC (self-test only)" in text
    assert "nothing was transmitted" in text


def test_the_command_refuses_an_invalid_manifest_with_a_nonzero_status(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    assert mapsheets.main(["--manifest", str(path)]) == 1
    assert "MANIFEST INVALID" in capsys.readouterr().err
