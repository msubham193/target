"""Schema-validation tests for the canonical observation contract. These run before any persistence exists."""

import copy
import itertools
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from geopoc.contracts import v2
from geopoc.contracts.flags import FLAGS, normalise_flag
from geopoc.contracts.observation import Observation, PixelRegion
from geopoc.contracts.review import (
    MEANINGS,
    TRANSITIONS,
    ReviewStatus,
    TransitionError,
    check_transition,
    initial_status,
)

ASSET = str(uuid.uuid4())
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
SHA = "ab" * 32


def region(coordinates=(120, 80, 420, 310), kind="bbox_xyxy", size=(640, 480)) -> dict:
    return {
        "type": kind,
        "coordinates": list(coordinates),
        "coordinate_space": "source_image_pixels",
        "origin": "top_left",
        "pixel_reference": "corner",
        "image_width": size[0],
        "image_height": size[1],
    }


def valid() -> dict:
    """A model detection in a ground photograph: located in the image, not on the Earth."""
    return {
        "observation_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "asset_id": ASSET,
        "pipeline": "ground_image",
        "model": {"name": "example-detector", "version": "0.1.0", "run_id": str(uuid.uuid4())},
        "claim": {
            "category": "coco:person",
            "description": "Model-predicted person-like feature.",
            "claim_type": "inferred",
        },
        "score": {"raw_value": 0.82, "semantics": "post-NMS class score", "calibrated": False, "value_range": [0, 1]},
        "evidence": [{"asset_id": ASSET, "region_px": region()}],
        "geospatial": {"geometry": None},
        "measurements": [],
        "quality_flags": ["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE"],
        "review": {"status": "needs_review"},
        "provenance": {"source_checksum": SHA, "ingested_at": NOW, "transformations": [], "created_at": NOW},
    }


def change(**paths) -> dict:
    """A valid record with nested values replaced, e.g. change(**{"score.raw_value": 2})."""
    record = valid()
    for path, value in paths.items():
        target = record
        *parents, last = path.split(".")
        for key in parents:
            target = target[int(key)] if key.isdigit() else target[key]
        target[last] = value
    return record


def rejects(record: dict, fragment: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Observation.model_validate(record)
    assert fragment in str(caught.value)


# --- the canonical schema --------------------------------------------------------------------------------------


def test_valid_observation_round_trips_through_json():
    observation = Observation.model_validate(valid())
    assert observation.schema_version == "3.0"
    again = Observation.model_validate_json(observation.model_dump_json())
    assert again == observation
    assert again.review.status is ReviewStatus.NEEDS_REVIEW


def test_unknown_fields_are_refused_not_ignored():
    rejects({**valid(), "priority": 1}, "Extra inputs are not permitted")
    rejects(change(**{"claim.weapon": "x"}), "Extra inputs are not permitted")


def test_every_v3_section_is_required():
    for section in (
        "model",
        "claim",
        "score",
        "evidence",
        "geospatial",
        "review",
        "provenance",
        "pipeline",
        "asset_id",
    ):
        record = valid()
        del record[section]
        rejects(record, section)


def test_timestamps_must_state_an_offset_and_are_stored_as_utc():
    rejects(change(**{"provenance.created_at": "2026-10-12T09:00:00"}), "must state its UTC offset")
    ist = datetime(2026, 10, 12, 14, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    observation = Observation.model_validate(change(**{"provenance.created_at": ist}))
    assert observation.provenance.created_at == NOW and observation.provenance.created_at.tzinfo is UTC


def test_source_checksum_must_be_a_sha256():
    rejects(change(**{"provenance.source_checksum": "abc"}), "SHA-256")


# --- evidence references ---------------------------------------------------------------------------------------


def test_at_least_one_evidence_reference_is_required():
    rejects(change(evidence=[]), "at least 1 item")
    record = valid()
    del record["evidence"]
    rejects(record, "evidence")


def test_evidence_must_reference_the_source_asset():
    other = str(uuid.uuid4())
    rejects(
        change(evidence=[{"asset_id": other, "region_px": region()}]), "must reference the observation's source asset"
    )
    # A second, corroborating asset is fine as long as the source asset is cited too.
    both = [{"asset_id": ASSET, "region_px": region()}, {"asset_id": other, "region_px": None}]
    assert len(Observation.model_validate(change(evidence=both)).evidence) == 2


def test_evidence_asset_must_be_an_identifier():
    rejects(change(evidence=[{"asset_id": "not-an-id", "region_px": region()}]), "asset_id")
    rejects(change(evidence=[{"region_px": region()}]), "asset_id")


def test_a_model_finding_in_an_image_must_point_to_a_region():
    rejects(change(evidence=[{"asset_id": ASSET, "region_px": None}]), "must point to an image region")


def test_video_evidence_needs_a_frame_or_timestamp_or_says_it_is_unknown():
    base = change(pipeline="video", evidence=[{"asset_id": ASSET, "region_px": region()}])
    rejects(base, "needs a frame or timestamp")
    timed = change(
        pipeline="video", evidence=[{"asset_id": ASSET, "region_px": region(), "frame_index": 12, "timestamp_ms": 400}]
    )
    assert Observation.model_validate(timed).evidence[0].frame_index == 12
    unknown = copy.deepcopy(base)
    unknown["quality_flags"].append("TIMESTAMP_UNKNOWN")
    assert Observation.model_validate(unknown)
    rejects(change(pipeline="video", evidence=[{"asset_id": ASSET, "frame_index": -1}]), "greater than or equal to 0")
    rejects(change(pipeline="video", evidence=[{"asset_id": ASSET, "timestamp_ms": 1.5}]), "timestamp_ms")


# --- pixel-coordinate convention ---------------------------------------------------------------------------------


def test_pixel_region_must_state_its_convention():
    for missing in ("coordinate_space", "origin", "pixel_reference", "image_width", "image_height"):
        data = region()
        del data[missing]
        with pytest.raises(ValidationError):
            PixelRegion.model_validate(data)
    for field, wrong in (
        ("coordinate_space", "preview_pixels"),
        ("origin", "bottom_left"),
        ("pixel_reference", "center"),
    ):
        with pytest.raises(ValidationError):
            PixelRegion.model_validate({**region(), field: wrong})


@pytest.mark.parametrize(
    ("coordinates", "fragment"),
    [
        ([120, 80, 420], "exactly"),
        ([420, 80, 120, 310], "x_min < x_max"),
        ([120, 310, 420, 80], "y_min < y_max"),
        ([120, 80, 120, 310], "x_min < x_max"),
        ([-1, 80, 420, 310], "outside the image"),
        ([120, 80, 641, 310], "outside the image"),
        ([120, 80, 420, 481], "outside the image"),
        ([120, 80, float("nan"), 310], "finite"),
        ([120, 80, "420", 310], "finite"),
        ([True, 80, 420, 310], "finite"),
    ],
)
def test_invalid_bounding_boxes_are_refused(coordinates, fragment):
    with pytest.raises(ValidationError) as caught:
        PixelRegion.model_validate(region(coordinates))
    assert fragment in str(caught.value)


def test_boxes_may_touch_the_image_edges_and_be_fractional():
    assert PixelRegion.model_validate(region([0, 0, 640, 480]))
    assert PixelRegion.model_validate(region([10.25, 20.5, 30.75, 40.0]))


def test_polygon_regions():
    assert PixelRegion.model_validate(region([[0, 0], [10, 0], [10, 10]], kind="polygon"))
    for bad in ([[0, 0], [10, 0]], [[0, 0], [10, 0], [10]], [[0, 0], [10, 0], [700, 10]]):
        with pytest.raises(ValidationError):
            PixelRegion.model_validate(region(bad, kind="polygon"))


# --- pixels never become geographic coordinates ---------------------------------------------------------------------

POINT = {"type": "Point", "coordinates": [75.0, 27.1]}
GEO = {
    "geometry": POINT,
    "crs_id": "EPSG:4326",
    "coordinate_units": "degree",
    "derivation_method": "raster_affine_transform",
}


@pytest.mark.parametrize("pipeline", ["ground_image", "video"])
def test_ground_and_video_observations_cannot_carry_geographic_coordinates(pipeline):
    record = change(pipeline=pipeline, geospatial=GEO, quality_flags=["UNCALIBRATED_SCORE"])
    record["evidence"][0]["frame_index"] = 0
    rejects(record, "cannot carry geographic coordinates")


def test_no_derivation_method_turns_pixels_into_coordinates():
    for method in ("pixel_coordinates", "image_position", "estimated", "exif_gps"):
        record = change(
            pipeline="overhead_raster",
            geospatial={**GEO, "derivation_method": method},
            quality_flags=["UNCALIBRATED_SCORE"],
        )
        rejects(record, "derivation_method must be one of")


def test_a_geometry_needs_crs_units_and_method_and_a_complete_crs_id():
    for missing in ("crs_id", "coordinate_units", "derivation_method"):
        rejects(
            change(pipeline="overhead_raster", geospatial={**GEO, missing: None}, quality_flags=["UNCALIBRATED_SCORE"]),
            "a geometry needs",
        )
    for crs in ("WGS84 + LCC", "WGS84", "4326"):
        rejects(
            change(pipeline="overhead_raster", geospatial={**GEO, "crs_id": crs}, quality_flags=["UNCALIBRATED_SCORE"]),
            "complete authority code",
        )


def test_georeferenced_raster_observation_is_accepted_and_flags_must_agree():
    located = change(pipeline="overhead_raster", geospatial=GEO, quality_flags=["UNCALIBRATED_SCORE"])
    assert Observation.model_validate(located).geospatial.crs_id == "EPSG:4326"
    rejects(change(pipeline="overhead_raster", geospatial=GEO), "contradicts the geometry")
    rejects(change(quality_flags=["UNCALIBRATED_SCORE"]), "must carry the NOT_GEOREFERENCED flag")
    rejects(change(geospatial={"geometry": None, "crs_id": "EPSG:4326"}), "must all be null")


# --- other retained rules ---------------------------------------------------------------------------------------------


def test_model_identity_is_required_for_model_results_only():
    rejects(change(model=None), "required unless the claim is human_entered")
    human = change(model=None, quality_flags=["NOT_GEOREFERENCED"], **{"claim.claim_type": "human_entered"})
    assert Observation.model_validate(human).model is None


def test_score_rules():
    rejects(change(**{"score.raw_value": 1.2}), "outside the range stated")
    rejects(change(**{"score.raw_value": float("inf")}), "finite")
    rejects(change(**{"score.raw_value": None, "score.calibrated": True}), "needs a value")
    rejects(change(**{"score.semantics": ""}), "semantics")
    rejects(change(quality_flags=["NOT_GEOREFERENCED"]), "must carry the UNCALIBRATED_SCORE flag")
    logit = change(**{"score.raw_value": 7.3, "score.value_range": None})
    assert Observation.model_validate(logit).score.raw_value == 7.3  # not every model scores in 0-1


def test_unclassified_category_must_be_flagged():
    rejects(change(**{"claim.category": "unclassified"}), "must carry the UNCLASSIFIED flag")
    flagged = change(
        quality_flags=["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE", "UNCLASSIFIED"], **{"claim.category": "unclassified"}
    )
    assert Observation.model_validate(flagged)
    rejects(change(**{"claim.category": "A Person!"}), "taxonomy id")


def test_measurements_carry_value_unit_and_method_or_are_unavailable():
    ok = {"name": "width", "value": 12.5, "unit": "px", "method": "bbox width", "validity": "valid"}
    assert Observation.model_validate(change(measurements=[ok]))
    assert Observation.model_validate(
        change(measurements=[{"name": "area", "value": None, "unit": None, "method": None, "validity": "unavailable"}])
    )
    rejects(change(measurements=[{**ok, "unit": None}]), "needs a finite value, a unit and a method")
    rejects(change(measurements=[{**ok, "validity": "unavailable"}]), "has no value")


def test_quality_flags_are_a_controlled_vocabulary():
    assert normalise_flag("missing_crs") == "MISSING_CRS"  # V3 lower-case example
    assert normalise_flag("UNKNOWN_CRS") == "MISSING_CRS"  # V2 synonym
    for code in ("LOW_CONFIDENCE", "BLUR", "METADATA_MISSING", "SOURCE_CONFLICT", "NOT_GEOREFERENCED"):  # V2 section 6
        assert code in FLAGS
    for code in ("blur", "low_resolution", "missing_crs", "timestamp_unknown", "metadata_incomplete"):  # V3 section 4
        assert normalise_flag(code) in FLAGS
    rejects(
        change(quality_flags=["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE", "HIGH_PRIORITY_TARGET"]),
        "unknown quality flag",
    )
    rejects(change(quality_flags=["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE", "not_georeferenced"]), "must not repeat")
    lower = Observation.model_validate(change(quality_flags=["not_georeferenced", "uncalibrated_score", "blur"]))
    assert lower.quality_flags == ["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE", "BLUR"]


# --- review states -----------------------------------------------------------------------------------------------------

ALL = list(ReviewStatus)


def test_exactly_the_six_version_3_states_exist_each_with_a_meaning():
    assert {s.value for s in ALL} == {"proposed", "needs_review", "verified", "inconclusive", "disputed", "rejected"}
    assert set(MEANINGS) == set(ALL)
    rejects(change(**{"review.status": "confirmed"}), "review.status")
    rejects(change(**{"review.status": "unresolved"}), "review.status")


def test_review_block_rules_per_state():
    reviewed = {"reviewed_at": NOW, "reviewed_by": "Asha"}
    for status in ("needs_review", "inconclusive", "disputed", "proposed"):
        assert Observation.model_validate(change(review={"status": status}))
    assert Observation.model_validate(change(review={"status": "verified", **reviewed}))
    assert Observation.model_validate(
        change(review={"status": "rejected", "reviewer_note": "Not a person; a statue.", **reviewed})
    )

    rejects(change(review={"status": "verified"}), "must record who concluded it and when")
    rejects(change(review={"status": "verified", "reviewed_at": NOW}), "must record who concluded it and when")
    rejects(change(review={"status": "rejected", **reviewed}), "must record the reason")
    rejects(change(review={"status": "proposed", **reviewed}), "has not been reviewed")


def ok(current, target, actor="reviewer", actor_id="Asha", note="because"):
    return check_transition(current, target, actor=actor, actor_id=actor_id, note=note)


def test_every_pair_of_states_is_either_permitted_or_refused():
    permitted = set(TRANSITIONS)
    for current, target in itertools.product(ALL, ALL):
        actors = TRANSITIONS[(current, target)].actors if (current, target) in permitted else set()
        for actor in ("reviewer", "system"):
            if actor in actors:
                assert ok(current, target, actor=actor)
            else:
                with pytest.raises(TransitionError):
                    ok(current, target, actor=actor)
    assert len(permitted) == 18


def test_no_change_is_not_a_transition():
    for status in ALL:
        with pytest.raises(TransitionError) as caught:
            ok(status, status)
        assert caught.value.code == "NO_CHANGE"


def test_only_a_reviewer_can_verify_and_the_system_never_can():
    for current in ALL:
        if (current, ReviewStatus.VERIFIED) in TRANSITIONS:
            assert TRANSITIONS[(current, ReviewStatus.VERIFIED)].actors == {"reviewer"}
    with pytest.raises(TransitionError) as caught:
        ok("proposed", "verified", actor="system", actor_id="validator")
    assert caught.value.code == "ACTOR_NOT_PERMITTED"


def test_who_made_the_change_is_always_recorded():
    for (current, target), rule in TRANSITIONS.items():
        actor = sorted(rule.actors)[0]
        for missing in (None, "", "   "):
            with pytest.raises(TransitionError) as caught:
                ok(current, target, actor=actor, actor_id=missing)
            assert caught.value.code == "ACTOR_REQUIRED"


def test_rejection_uncertainty_disagreement_and_pending_review_stay_distinct():
    # Rejection always records a reason and is never terminal-by-deletion: it can only be reopened to review.
    for current in ("proposed", "needs_review", "inconclusive", "disputed"):
        with pytest.raises(TransitionError) as caught:
            ok(current, "rejected", note=" ")
        assert caught.value.code == "NOTE_REQUIRED"
    assert {t for (c, t) in TRANSITIONS if c is ReviewStatus.REJECTED} == {ReviewStatus.NEEDS_REVIEW}

    # Uncertainty cannot be asserted away: inconclusive never goes straight to verified.
    with pytest.raises(TransitionError) as caught:
        ok("inconclusive", "verified")
    assert caught.value.code == "INVALID_TRANSITION"
    with pytest.raises(TransitionError):
        ok("needs_review", "inconclusive", note=None)

    # Disagreement is raised only by conflict detection, and leaving it always needs a documented resolution.
    for current in ALL:
        if (current, ReviewStatus.DISPUTED) in TRANSITIONS:
            assert TRANSITIONS[(current, ReviewStatus.DISPUTED)].actors == {"system"}
    for target in ("needs_review", "verified", "rejected"):
        with pytest.raises(TransitionError) as caught:
            ok("disputed", target, note=None)
        assert caught.value.code == "NOTE_REQUIRED"
    with pytest.raises(TransitionError):
        ok("disputed", "verified", actor="system", actor_id="rule-1")

    # Pending review concludes only through a reviewer.
    assert ok("needs_review", "verified", note=None)
    with pytest.raises(TransitionError):
        ok("needs_review", "proposed")

    # A concluded finding is reopened with a reason, never flipped directly.
    with pytest.raises(TransitionError):
        ok("verified", "rejected")
    with pytest.raises(TransitionError):
        ok("rejected", "verified")
    with pytest.raises(TransitionError) as caught:
        ok("verified", "needs_review", note="")
    assert caught.value.code == "NOTE_REQUIRED"


def test_initial_state_is_set_by_policy_never_by_the_model():
    flags = ["NOT_GEOREFERENCED"]
    assert initial_status(quality_flags=flags, score_calibrated=True) is ReviewStatus.PROPOSED
    assert initial_status(quality_flags=flags, score_calibrated=False) is ReviewStatus.NEEDS_REVIEW  # CON-002
    assert initial_status(quality_flags=flags, score_calibrated=True, below_threshold=True) is ReviewStatus.NEEDS_REVIEW
    assert initial_status(quality_flags=["BLUR"], score_calibrated=True) is ReviewStatus.NEEDS_REVIEW
    assert (
        initial_status(quality_flags=flags, score_calibrated=True, input_cannot_support_claim=True)
        is ReviewStatus.INCONCLUSIVE
    )
    assert initial_status(quality_flags=flags, score_calibrated=True, in_open_conflict=True) is ReviewStatus.DISPUTED
    for kwargs in ({}, {"below_threshold": True}, {"input_cannot_support_claim": True}, {"in_open_conflict": True}):
        for calibrated in (True, False):
            status = initial_status(quality_flags=flags, score_calibrated=calibrated, **kwargs)
            assert status not in (ReviewStatus.VERIFIED, ReviewStatus.REJECTED)


# --- Version 2.0 to Version 3.0 --------------------------------------------------------------------------------------------

V2_FIELDS_IN_SPEC = {
    "observation_id", "project_id", "asset_id", "pipeline_id", "model_name", "model_version", "run_id", "category",
    "score", "review_status", "geometry_image_px", "geometry_geo", "frame_number", "timestamp_ms", "measurements",
    "evidence_refs", "quality_flags", "summary", "created_at", "reviewed_at", "reviewer_note",
}  # fmt: skip


def v2_example() -> dict:
    """The specification's own V2 example (section 6.1), with real identifiers in place of the demo strings."""
    return {
        "observation_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "asset_id": ASSET,
        "pipeline_id": "ground_image",
        "model_name": "example-detector",
        "model_version": "0.1.0",
        "run_id": str(uuid.uuid4()),
        "category": "building",
        "score": 0.82,
        "review_status": "needs_review",
        "geometry_image_px": {
            "type": "bbox_xyxy",
            "coordinates": [120, 80, 420, 310],
            "coordinate_space": "source_image_pixels",
        },
        "geometry_geo": None,
        "evidence_refs": [{"asset_id": ASSET, "region": "bbox_xyxy"}],
        "quality_flags": ["NOT_GEOREFERENCED"],
        "measurements": None,
        "summary": "Model-predicted building-like feature; geographic location not established.",
        "created_at": NOW,
    }


CONTEXT = {
    "source_checksum": SHA,
    "ingested_at": NOW,
    "score_semantics": "not stated in the V2 record",
    "image_size": (640, 480),
}


def test_every_v2_field_is_accounted_for_and_none_is_dropped():
    assert set(v2.FIELD_MAP) == V2_FIELDS_IN_SPEC
    dispositions = {disposition for disposition, _, _ in v2.FIELD_MAP.values()}
    assert dispositions <= {"retained", "mapped", "dropped"}
    assert "dropped" not in dispositions


def test_the_specifications_v2_example_converts_without_losing_information():
    source = v2_example()
    observation = v2.from_v2(source, **CONTEXT)

    assert str(observation.observation_id) == source["observation_id"]
    assert str(observation.project_id) == source["project_id"]
    assert str(observation.asset_id) == source["asset_id"]
    assert observation.pipeline == "ground_image"
    assert (observation.model.name, observation.model.version) == ("example-detector", "0.1.0")
    assert str(observation.model.run_id) == source["run_id"]
    assert observation.claim.category == "building"
    assert observation.claim.description == source["summary"]
    assert observation.score.raw_value == 0.82 and observation.score.calibrated is False
    assert observation.review.status is ReviewStatus.NEEDS_REVIEW
    box = observation.evidence[0].region_px
    assert (box.type, box.coordinates, box.coordinate_space) == (
        "bbox_xyxy",
        [120, 80, 420, 310],
        "source_image_pixels",
    )
    assert observation.geospatial.geometry is None
    assert "NOT_GEOREFERENCED" in observation.quality_flags
    assert observation.provenance.created_at == NOW


@pytest.mark.parametrize(
    ("v2_status", "v3_status"),
    [
        ("predicted", "proposed"),
        ("needs_review", "needs_review"),
        ("confirmed", "verified"),
        ("rejected", "rejected"),
        ("unresolved", "needs_review"),
    ],
)
def test_v2_review_states_map_onto_the_six(v2_status, v3_status):
    source = v2_example() | {"review_status": v2_status}
    extra = {}
    if v3_status in ("verified", "rejected"):
        source |= {"reviewed_at": NOW, "reviewer_note": "checked"}
        extra = {"reviewed_by": "Asha"}
    assert v2.from_v2(source, **CONTEXT, **extra).review.status.value == v3_status
    assert set(v2.REVIEW_STATUS_MAP) == {"predicted", "needs_review", "confirmed", "rejected", "unresolved"}


def test_v2_video_measurement_and_flag_fields_carry_across():
    source = v2_example() | {
        "pipeline_id": "video",
        "frame_number": 42,
        "timestamp_ms": 1400.4,
        "measurements": {"value": 300, "unit": "px", "method": "bbox width"},
        "quality_flags": ["UNKNOWN_CRS", "LOW_CONFIDENCE", "NOT_GEOREFERENCED"],
        "reviewer_note": "looks plausible",
    }
    observation = v2.from_v2(source, **CONTEXT)
    assert (observation.evidence[0].frame_index, observation.evidence[0].timestamp_ms) == (42, 1400)
    assert (observation.measurements[0].value, observation.measurements[0].unit) == (300, "px")
    assert observation.quality_flags[:2] == ["MISSING_CRS", "LOW_CONFIDENCE"]
    assert observation.review.reviewer_note == "looks plausible"


def test_v2_rules_are_enforced_during_conversion():
    with pytest.raises(ValidationError):  # at least one source reference
        v2.from_v2(v2_example() | {"evidence_refs": []}, **CONTEXT)
    unknown = v2.from_v2(v2_example() | {"category": "spaceship"}, **CONTEXT, known_categories={"building"})
    assert unknown.claim.category == "unclassified" and "UNCLASSIFIED" in unknown.quality_flags
    with pytest.raises(ValidationError):  # the V2 0-1 score range
        v2.from_v2(v2_example() | {"score": 1.7}, **CONTEXT)
    with pytest.raises(ValueError, match="not defined in the Version 2.0 contract"):
        v2.from_v2(v2_example() | {"target_priority": 1}, **CONTEXT)
    with pytest.raises(ValueError, match="image_size is needed"):
        v2.from_v2(v2_example(), **(CONTEXT | {"image_size": None}))


def test_v2_geographic_geometry_is_never_completed_by_assumption():
    located = v2_example() | {
        "pipeline_id": "overhead_raster",
        "geometry_geo": {"geometry": POINT, "crs_id": "EPSG:4326"},
        "quality_flags": [],
    }
    with pytest.raises(ValueError, match="needs its coordinate_units and derivation_method supplied"):
        v2.from_v2(located, **CONTEXT)
    observation = v2.from_v2(
        located, **CONTEXT, geo={"coordinate_units": "degree", "derivation_method": "raster_affine_transform"}
    )
    assert observation.geospatial.crs_id == "EPSG:4326"
    # ... and a ground photograph still cannot have one, whatever the V2 record says.
    with pytest.raises(ValidationError, match="cannot carry geographic coordinates"):
        v2.from_v2(
            located | {"pipeline_id": "ground_image"},
            **CONTEXT,
            geo={"coordinate_units": "degree", "derivation_method": "raster_affine_transform"},
        )
