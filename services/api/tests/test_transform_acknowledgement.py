"""Transformation accuracy policy (CRS correctness hardening, Phase 2).

A datum shift whose documented accuracy is worse than 10 m, or not stated at all, is not used until a person has
acknowledged that specific operation. Exactly 10 m needs no acknowledgement. A ballpark is recognised by its structure
and is never unlocked by an acknowledgement, and neither is a missing grid. An acknowledgement belongs to one
operation, one pair of CRSs and one accuracy value, and for stored data it can only come from what was recorded with the
georeferencing: a request cannot supply it.

The planner cases use stand-in operations on a CRS pair no other test uses. The API cases use synthetic rasters.
"""

import json
import logging

import pyproj
import pytest

from geopoc.geo import convert, georeference
from geopoc.geo import transform as T
from tests.test_crs_assessment import DELHI, geotiff_near, put
from tests.test_georeferencing import record
from tests.test_transform_consistency import convert_point, pixel

WGS84 = "EPSG:4326"
CODE = "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"
# A pair no other test resolves, so the planner's cache holds only what these tests put there.
SOURCE, TARGET = "EPSG:2056", WGS84


def op(op_id, klass="approximate", accuracy=3.0, grids=()):
    return T.Operation(
        id=op_id,
        name=f"stand-in {op_id}",
        klass=klass,
        accuracy_m=accuracy,
        authority_code=op_id,
        area_of_use=None,
        grids=tuple(grids),
        pipeline=f"+proj=noop {op_id}",
    )


@pytest.fixture
def planned(monkeypatch):
    """Make the planner offer exactly the given operations for SOURCE -> TARGET."""

    def offer(*operations):
        monkeypatch.setattr(T, "_group", lambda *_args: tuple(operations))
        T._plan.cache_clear()

    yield offer
    T._plan.cache_clear()


def ack(operation, source=SOURCE, target=TARGET, accuracy="same"):
    return T.Acknowledgement(
        operation.id,
        T.crs_label(pyproj.CRS(source)),
        T.datum_label(pyproj.CRS(target)),
        operation.accuracy_m if accuracy == "same" else accuracy,
    )


# --- the 10 m threshold -------------------------------------------------------------------------------------------------


def test_the_threshold_is_ten_metres():
    assert T.ACK_ACCURACY_M == 10.0


@pytest.mark.parametrize(
    ("accuracy", "reason"),
    [
        (0.5, None),
        (3.0, None),
        (10.0, None),  # exactly the limit: allowed
        (10.0001, T.ACK_ABOVE_THRESHOLD),
        (999.0, T.ACK_ABOVE_THRESHOLD),
        (None, T.ACK_NOT_STATED),  # a registered shift that states no accuracy is not a ballpark, and is not trusted
    ],
)
def test_an_approximate_operation_needs_acknowledgement_only_above_ten_metres_or_when_unstated(accuracy, reason):
    operation = op("EPSG:1", accuracy=accuracy)
    assert operation.ack_reason == reason and operation.requires_acknowledgement is (reason is not None)


def test_exact_ballpark_and_unavailable_operations_never_ask_for_acknowledgement():
    assert op("datum-unchanged", "exact", 0.0).ack_reason is None
    assert op("ballpark", "ballpark", None).ack_reason is None  # it is blocked outright, not acknowledged
    assert op("EPSG:9", "unavailable", 999.0).ack_reason is None


def test_resolving_at_the_limit_needs_nothing_and_just_above_it_needs_an_acknowledgement(planned):
    planned(op("EPSG:10", accuracy=10.0))
    assert T.resolve(SOURCE, TARGET).acknowledgement is None
    planned(op("EPSG:11", accuracy=10.0001))
    with pytest.raises(T.TransformationError) as refused:
        T.resolve(SOURCE, TARGET)
    assert (refused.value.status, refused.value.code) == (409, CODE)
    assert refused.value.extra == {
        "operation_id": "EPSG:11",
        "operation_name": "stand-in EPSG:11",
        "accuracy_m": 10.0001,
        "threshold_m": 10.0,
        "reason": T.ACK_ABOVE_THRESHOLD,
        "source_crs": "EPSG:2056",
        "target_crs": "EPSG:4326",
        "stale_acknowledgement": False,
    }


def test_unknown_accuracy_is_refused_until_acknowledged_and_is_never_chosen_for_the_person(planned):
    unknown = op("EPSG:12", accuracy=None)
    planned(unknown)
    with pytest.raises(T.TransformationError) as refused:
        T.resolve(SOURCE, TARGET)
    assert refused.value.code == CODE and refused.value.extra["reason"] == T.ACK_NOT_STATED
    assert "not stated" in refused.value.detail
    resolved = T.resolve(SOURCE, TARGET, acknowledgement=ack(unknown))
    assert resolved.operation.id == "EPSG:12" and resolved.acknowledgement is not None


# --- an acknowledgement is bound to what was acknowledged ---------------------------------------------------------------


def test_an_acknowledgement_matches_only_the_same_operation_crs_and_accuracy(planned):
    shift = op("EPSG:13", accuracy=50.0)
    planned(shift)
    assert T.resolve(SOURCE, TARGET, acknowledgement=ack(shift)).acknowledgement is not None
    stale = {
        "another operation": T.Acknowledgement("EPSG:99", "EPSG:2056", "EPSG:4326", 50.0),
        "another source CRS": T.Acknowledgement("EPSG:13", "EPSG:2057", "EPSG:4326", 50.0),
        "another target": T.Acknowledgement("EPSG:13", "EPSG:2056", "EPSG:4258", 50.0),
        "a different accuracy": ack(shift, accuracy=5.0),
        "an unstated accuracy": ack(shift, accuracy=None),
    }
    for why, wrong in stale.items():
        with pytest.raises(T.TransformationError) as refused:
            T.resolve(SOURCE, TARGET, acknowledgement=wrong)
        assert refused.value.code == CODE, why
        assert refused.value.extra["stale_acknowledgement"] is True, why


def test_a_registry_that_now_states_another_accuracy_makes_an_old_acknowledgement_stale(planned):
    planned(op("EPSG:14", accuracy=50.0))
    old = ack(op("EPSG:14", accuracy=50.0))
    planned(op("EPSG:14", accuracy=80.0))
    with pytest.raises(T.TransformationError) as refused:
        T.resolve(SOURCE, TARGET, acknowledgement=old)
    assert refused.value.code == CODE and refused.value.extra["accuracy_m"] == 80.0


def test_an_acknowledgement_of_a_shift_to_wgs84_also_covers_web_mercator_tiles():
    kalianpur = T.plan("EPSG:24313", WGS84)
    shift = kalianpur.usable[0]
    given = T.Acknowledgement(shift.id, "EPSG:24313", "EPSG:4326", shift.accuracy_m)
    assert T.resolve("EPSG:24313", "EPSG:3857", acknowledgement=given).operation.id == shift.id


# --- what an acknowledgement can never unlock -----------------------------------------------------------------------------


def test_an_acknowledgement_does_not_unlock_a_ballpark(planned):
    rough = op("ballpark", "ballpark", None)
    planned(rough)
    with pytest.raises(T.TransformationError) as refused:
        T.resolve(SOURCE, TARGET, acknowledgement=ack(rough))
    assert refused.value.code == "TRANSFORMATION_BALLPARK_BLOCKED"
    with pytest.raises(T.TransformationError) as named:
        T.resolve(SOURCE, TARGET, operation_id="ballpark", acknowledgement=ack(rough))
    assert named.value.code == "TRANSFORMATION_BALLPARK_BLOCKED"


def test_an_acknowledgement_does_not_unlock_a_missing_grid(planned):
    needs_grid = op("EPSG:15", "unavailable", 30.0, grids=[T.Grid("nowhere.gsb", False)])
    planned(needs_grid)
    with pytest.raises(T.TransformationError) as refused:
        T.resolve(SOURCE, TARGET, operation_id="EPSG:15", acknowledgement=ack(needs_grid))
    assert refused.value.code == "TRANSFORMATION_GRID_MISSING"


def test_several_operations_still_need_an_explicit_choice_and_the_choice_may_need_acknowledging(planned):
    coarse, fine = op("EPSG:16", accuracy=120.0), op("EPSG:17", accuracy=4.0)
    planned(coarse, fine)
    with pytest.raises(T.TransformationError) as undecided:
        T.resolve(SOURCE, TARGET)
    assert undecided.value.code == "TRANSFORMATION_SELECTION_REQUIRED"  # nothing preselected
    assert T.resolve(SOURCE, TARGET, operation_id="EPSG:17").acknowledgement is None
    with pytest.raises(T.TransformationError) as needs:
        T.resolve(SOURCE, TARGET, operation_id="EPSG:16")
    assert needs.value.code == CODE and needs.value.extra["operation_id"] == "EPSG:16"
    assert T.resolve(SOURCE, TARGET, operation_id="EPSG:16", acknowledgement=ack(coarse)).acknowledgement is not None


# --- ballpark is recognised by structure, not by wording -----------------------------------------------------------------


class Step:
    def __init__(self, has_ballpark):
        self.has_ballpark_transformation = has_ballpark


class FakeTransformer:
    """Just the parts of a pyproj Transformer that classification reads."""

    def __init__(self, description, accuracy, steps, name=None):
        self.description, self.accuracy, self.operations = description, accuracy, steps
        self.definition = "+proj=noop"
        self.area_of_use = None
        self._name = name or description

    def to_json(self):
        return json.dumps({"name": self._name})


def classify(transformer):
    return T._from_transformer(transformer)  # noqa: SLF001 - the classification under test


def test_a_ballpark_is_recognised_by_structure_even_when_its_name_says_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="geopoc.geo.transform"):
        found = classify(FakeTransformer("Datum shift for the area", 3.0, [Step(False), Step(True)]))
    assert found.klass == "ballpark" and found.ballpark_signal == "structural" and found.accuracy_m is None
    assert any("ballpark by structure" in r.getMessage() for r in caplog.records)  # the mismatch is logged


def test_wording_alone_still_fails_closed_and_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="geopoc.geo.transform"):
        found = classify(FakeTransformer("Ballpark geographic offset", 3.0, [Step(False)]))
    assert found.klass == "ballpark" and found.ballpark_signal == "text"
    assert any("wording" in r.getMessage() for r in caplog.records)


def test_structure_and_wording_agreeing_is_a_ballpark_without_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="geopoc.geo.transform"):
        found = classify(FakeTransformer("Ballpark geographic offset", None, [Step(True)]))
    assert found.klass == "ballpark" and found.ballpark_signal == "both" and not caplog.records


def test_unknown_accuracy_is_not_a_ballpark():
    for accuracy in (None, -1.0):
        found = classify(FakeTransformer("A registered shift", accuracy, [Step(False)]))
        assert found.klass == "approximate" and found.accuracy_m is None and found.ballpark_signal is None
        assert found.requires_acknowledgement and found.ack_reason == T.ACK_NOT_STATED


def test_zero_accuracy_is_exact_and_a_stated_accuracy_is_kept():
    assert classify(FakeTransformer("UTM", 0.0, [Step(False)])).klass == "exact"
    shift = classify(FakeTransformer("Helmert", 10.0, [Step(False)]))
    assert (shift.klass, shift.accuracy_m, shift.requires_acknowledgement) == ("approximate", 10.0, False)


def test_the_real_registry_agrees_with_the_structural_test():
    ballpark = T.plan("EPSG:24372", WGS84)  # a registered projection with no registered shift to WGS 84
    assert [(o.klass, o.ballpark_signal) for o in ballpark.operations] == [("ballpark", "both")]
    assert T.plan("EPSG:32643", WGS84).usable[0].klass == "exact"
    assert T.plan("EPSG:24313", WGS84).usable[0].ack_reason == T.ACK_ABOVE_THRESHOLD


# --- through the application ---------------------------------------------------------------------------------------------


def kalianpur(client, project, headers, run_jobs):
    """A file-georeferenced raster whose only datum shift is 999 m."""
    return put(client, project, headers, run_jobs, geotiff_near("EPSG:24313", *DELHI, size=(40, 30)))


def test_a_file_georeferenced_raster_that_needs_acknowledgement_is_blocked_everywhere(
    client, project, headers, run_jobs
):
    asset = kalianpur(client, project, headers, run_jobs)
    assert CODE in asset["quality_flags"]  # said at ingestion, with the reason
    assert "acknowledg" in asset["unknowns"]["transformation"]

    layers = client.get(f"/api/v1/projects/{project['id']}/layers").json()
    assert not layers["rasters"]
    [excluded] = [e for e in layers["excluded"] if e["asset_id"] == asset["id"]]
    assert excluded["reason"] == "TRANSFORMATION_BLOCKED" and "10" in excluded["detail"]

    tile = client.get(f"/api/v1/assets/{asset['id']}/tiles/6/42/26.png")
    assert tile.status_code == 409 and tile.json()["code"] == CODE
    assert tile.json()["context"]["accuracy_m"] == 999.0 and tile.json()["context"]["threshold_m"] == 10.0

    by_position = pixel(client, asset, lon=DELHI[0], lat=DELHI[1])
    assert by_position.status_code == 409 and by_position.json()["code"] == CODE
    by_pixel = pixel(client, asset, col=10, row=5).json()
    assert by_pixel["lon_lat"] is None and "acknowledge" in by_pixel["lon_lat_note"].lower()
    assert by_pixel["native"] is not None  # the pixel's own coordinates are not a conversion


def test_a_request_cannot_acknowledge_for_stored_data(client, project, headers, run_jobs):
    asset = kalianpur(client, project, headers, run_jobs)
    for extra in (
        {"acknowledge_operation": "EPSG:1247", "acknowledge_accuracy_m": 999.0},
        {"acknowledge_transformation": "true", "operation": "EPSG:1247"},
    ):
        assert pixel(client, asset, lon=DELHI[0], lat=DELHI[1], **extra).status_code == 409
        tile = client.get(f"/api/v1/assets/{asset['id']}/tiles/6/42/26.png", params=extra)
        assert tile.status_code == 409 and tile.json()["code"] == CODE
    plan = client.get(f"/api/v1/assets/{asset['id']}/crs-assessment").json()
    assert plan["transformations"][0]["blocked"]["code"] == CODE


def test_the_stateless_calculator_takes_a_per_request_acknowledgement_and_stores_nothing(client):
    point = {"source": "EPSG:24313", "target": WGS84, "x": 500000, "y": 3170000}
    refused = convert_point(client, **point)
    assert refused.status_code == 409 and refused.json()["code"] == CODE
    assert "acknowledge_operation=EPSG:1247" in refused.json()["detail"]
    accepted = convert_point(client, **point, acknowledge_operation="EPSG:1247", acknowledge_accuracy_m=999.0).json()
    assert accepted["transformation"]["accuracy_m"] == 999.0 and "ACKNOWLEDGED_BY_REQUEST" in accepted["warnings"]
    again = convert_point(client, **point)  # nothing was remembered
    assert again.status_code == 409
    stale = convert_point(client, **point, acknowledge_operation="EPSG:1247", acknowledge_accuracy_m=5.0)
    assert stale.status_code == 409 and stale.json()["context"]["stale_acknowledgement"] is True
    unnamed = convert_point(client, **point, acknowledge_operation="EPSG:1247")  # accuracy not given: it must match
    assert unnamed.status_code == 409
    assert convert_point(client, "EPSG:32643", WGS84, 500000, 3000000).status_code == 200  # exact: unaffected


def test_the_calculator_cannot_acknowledge_a_ballpark(client):
    point = {"source": "EPSG:24372", "target": WGS84, "x": 1000000, "y": 1000000}
    blocked = convert_point(client, **point, acknowledge_operation="ballpark")
    assert blocked.status_code == 409 and blocked.json()["code"] == "TRANSFORMATION_BALLPARK_BLOCKED"


def unreferenced_kalianpur(client, project, headers, run_jobs):
    """A raster with a geotransform in the right place but no CRS, so a person can record one for it."""
    from affine import Affine

    from tests import media

    x, y = pyproj.Transformer.from_crs(WGS84, "EPSG:24313", always_xy=True).transform(*DELHI)
    data = media.geotiff_bytes(
        crs=None, transform=Affine(10, 0, round(x / 10) * 10, 0, -10, round(y / 10) * 10), size=(80, 80)
    )
    return put(client, project, headers, run_jobs, data)


def test_recording_a_georeferencing_needs_the_acknowledgement_and_stores_it_with_the_version(
    client, project, headers, run_jobs
):
    asset = unreferenced_kalianpur(client, project, headers, run_jobs)
    body = {"operation": "assign_crs", "crs_id": "EPSG:24313", "reason": "Stated in the delivery note."}
    refused = record(client, headers, asset, **body)
    assert refused.status_code == 422
    assert any(e["field"] == CODE for e in refused.json()["errors"])

    recorded = record(client, headers, asset, **body, acknowledge_transformation=True)
    assert recorded.status_code == 201, recorded.text
    stored = recorded.json()["history"][-1]["validation"]["transformation"]  # the version just recorded
    given = stored["acknowledgement"]
    assert given["operation_id"] == "EPSG:1247" and given["accuracy_m"] == 999.0 and given["threshold_m"] == 10.0
    assert given["source_crs"] == "EPSG:24313" and given["target_crs"] == "EPSG:4326"
    assert given["acknowledged_by"] and given["acknowledged_at"]  # who and when are part of the version
    assert stored["operation"]["acknowledgement"]["operation_id"] == "EPSG:1247"

    # The recorded version carries it, so every path now works without the browser saying anything.
    looked_up = pixel(client, asset, col=10, row=5).json()
    assert looked_up["lon_lat"] is not None and looked_up["transformation"]["accuracy_m"] == 999.0
    assert convert.recorded_acknowledgement(_raster_of(asset)).operation_id == "EPSG:1247"
    layers = client.get(f"/api/v1/projects/{project['id']}/layers").json()
    [layer] = [r for r in layers["rasters"] if r["asset_id"] == asset["id"]]
    assert layer["transformation"]["requires_acknowledgement"] and layer["transformation"]["acknowledged"]


def _raster_of(asset):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from geopoc.db.models import Asset
    from geopoc.db.session import get_engine

    with Session(get_engine()) as session:
        stored = session.scalars(select(Asset).where(Asset.id == asset["id"])).unique().one()
        session.expunge_all()
        return stored.raster


def test_a_recorded_version_without_an_acknowledgement_stays_blocked(client, project, headers, run_jobs, monkeypatch):
    asset = unreferenced_kalianpur(client, project, headers, run_jobs)
    body = {"operation": "assign_crs", "crs_id": "EPSG:24313", "reason": "Stated in the delivery note."}
    assert record(client, headers, asset, **body, acknowledge_transformation=True).status_code == 201
    # A version recorded before acknowledgements existed has none, and history cannot be amended to add one.
    monkeypatch.setattr(convert, "recorded_acknowledgement", lambda raster: None)
    layers = client.get(f"/api/v1/projects/{project['id']}/layers").json()
    assert not any(r["asset_id"] == asset["id"] for r in layers["rasters"])
    blocked = pixel(client, asset, lon=DELHI[0], lat=DELHI[1])
    assert blocked.status_code == 409 and blocked.json()["code"] == CODE
    tile = client.get(f"/api/v1/assets/{asset['id']}/tiles/6/42/26.png")
    assert tile.status_code == 409 and tile.json()["code"] == CODE


def test_no_acknowledgement_is_written_anywhere_but_the_georeferencing_version_and_the_aoi(client, headers):
    from tests.test_assets import rows

    before = rows("SELECT count(*) FROM audit_event WHERE details::text ILIKE '%acknowledg%'")[0][0]
    point = {"source": "EPSG:24313", "target": WGS84, "x": 500000, "y": 3170000}
    convert_point(client, **point, acknowledge_operation="EPSG:1247", acknowledge_accuracy_m=999.0)
    assert rows("SELECT count(*) FROM audit_event WHERE details::text ILIKE '%acknowledg%'")[0][0] == before
    assert georeference.Proposal(operation="assign_crs").acknowledge_transformation is False  # off unless asked for
