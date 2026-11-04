"""Conversion of a Version 2.0 observation record (V2 section 6) into the canonical Version 3.0 contract.

Every V2 field is accounted for in FIELD_MAP as retained, mapped or dropped. Nothing is dropped at present.
A V2 record does not carry everything V3 requires, so the caller supplies the missing provenance.
"""

from datetime import datetime
from typing import Any

from geopoc.contracts.observation import UNCLASSIFIED, Observation

# V2 field -> (disposition, V3 location, note)
FIELD_MAP: dict[str, tuple[str, str, str]] = {
    "observation_id": ("retained", "observation_id", ""),
    "project_id": ("retained", "project_id", ""),
    "asset_id": ("retained", "asset_id", ""),
    "pipeline_id": ("mapped", "pipeline", "renamed"),
    "model_name": ("mapped", "model.name", "nested"),
    "model_version": ("mapped", "model.version", "nested"),
    "run_id": ("mapped", "model.run_id", "nested"),
    "category": ("mapped", "claim.category", "unknown class becomes 'unclassified' with the UNCLASSIFIED flag"),
    "score": ("mapped", "score.raw_value", "semantics and calibration must be supplied; V2 states neither"),
    "review_status": ("mapped", "review.status", "predicted>proposed, confirmed>verified, unresolved>needs_review"),
    "geometry_image_px": ("mapped", "evidence[].region_px", "pixel convention retained on the region"),
    "geometry_geo": ("mapped", "geospatial", "accepted only with CRS, units and derivation method"),
    "frame_number": ("mapped", "evidence[].frame_index", "renamed"),
    "timestamp_ms": ("mapped", "evidence[].timestamp_ms", "rounded to whole milliseconds"),
    "measurements": ("mapped", "measurements[]", "single value becomes a one-item list"),
    "evidence_refs": ("mapped", "evidence[]", "the 'at least one' rule is retained"),
    "quality_flags": ("mapped", "quality_flags", "codes normalised through the flag registry"),
    "summary": ("mapped", "claim.description", "same purpose; one field instead of two"),
    "created_at": ("mapped", "provenance.created_at", "UTC rule retained"),
    "reviewed_at": ("mapped", "review.reviewed_at", "UTC rule retained"),
    "reviewer_note": ("mapped", "review.reviewer_note", "kept separate from model output"),
}

REVIEW_STATUS_MAP = {
    "predicted": "proposed",
    "needs_review": "needs_review",
    "confirmed": "verified",
    "rejected": "rejected",
    # V2 `unresolved` meant "not yet settled by review". V3 `inconclusive` and `disputed` are narrower
    # (the input cannot support the claim; sources conflict), so the faithful target is pending review.
    "unresolved": "needs_review",
}


def from_v2(
    record: dict[str, Any],
    *,
    source_checksum: str,
    ingested_at: datetime,
    score_semantics: str,
    score_calibrated: bool = False,
    claim_type: str = "inferred",
    image_size: tuple[int, int] | None = None,
    known_categories: set[str] | None = None,
    geo: dict[str, str] | None = None,
    reviewed_by: str | None = None,
) -> Observation:
    """Build a canonical observation from a V2 record. Raises on unknown V2 fields rather than ignoring them."""
    unknown = set(record) - set(FIELD_MAP)
    if unknown:
        raise ValueError(f"fields not defined in the Version 2.0 contract: {sorted(unknown)}")

    flags = list(record.get("quality_flags") or [])

    category = record.get("category")
    if not category or (known_categories is not None and category not in known_categories):
        category = UNCLASSIFIED
        if "UNCLASSIFIED" not in flags:
            flags.append("UNCLASSIFIED")

    region = None
    v2_region = record.get("geometry_image_px")
    if v2_region is not None:
        if image_size is None:
            raise ValueError("image_size is needed to carry a V2 pixel geometry across")
        region = {
            "type": v2_region["type"],
            "coordinates": v2_region["coordinates"],
            "coordinate_space": v2_region["coordinate_space"],
            "origin": "top_left",
            "pixel_reference": "corner",
            "image_width": image_size[0],
            "image_height": image_size[1],
        }

    timestamp = record.get("timestamp_ms")
    evidence = [
        {
            "asset_id": ref["asset_id"],
            # V2 `region` names the kind of region; attach the geometry when it is the one described.
            "region_px": region if region and ref.get("region") == region["type"] else None,
            "frame_index": record.get("frame_number"),
            "timestamp_ms": round(timestamp) if timestamp is not None else None,
        }
        for ref in record.get("evidence_refs") or []
    ]

    geospatial: dict[str, Any] = {"geometry": None}
    v2_geo = record.get("geometry_geo")
    if v2_geo is not None:
        # V2 says "Geometry + CRS"; units and method are not in the record and are never assumed.
        if not geo or not {"coordinate_units", "derivation_method"} <= set(geo):
            raise ValueError("a V2 geographic geometry needs its coordinate_units and derivation_method supplied")
        geospatial = {
            "geometry": v2_geo.get("geometry"),
            "crs_id": v2_geo.get("crs_id"),
            "coordinate_units": geo["coordinate_units"],
            "derivation_method": geo["derivation_method"],
        }
    elif "NOT_GEOREFERENCED" not in flags:
        flags.append("NOT_GEOREFERENCED")

    if not score_calibrated and claim_type == "inferred" and "UNCALIBRATED_SCORE" not in flags:
        flags.append("UNCALIBRATED_SCORE")

    measurement = record.get("measurements")
    measurements = []
    if measurement is not None:
        measurements.append(
            {
                "name": measurement.get("name", "measurement"),
                "value": measurement["value"],
                "unit": measurement["unit"],
                "method": measurement["method"],
                "validity": "valid",
            }
        )

    status = REVIEW_STATUS_MAP[record["review_status"]]
    reviewed_at = record.get("reviewed_at")
    model = None
    if record.get("model_name") is not None:
        model = {"name": record["model_name"], "version": record["model_version"], "run_id": record["run_id"]}

    return Observation.model_validate(
        {
            "observation_id": record["observation_id"],
            "project_id": record["project_id"],
            "asset_id": record["asset_id"],
            "pipeline": record["pipeline_id"],
            "model": model,
            "claim": {"category": category, "description": record["summary"], "claim_type": claim_type},
            "score": {
                "raw_value": record.get("score"),
                "semantics": score_semantics,
                "calibrated": score_calibrated,
                "value_range": (0.0, 1.0),  # the V2 contract's own range rule
            },
            "evidence": evidence,
            "geospatial": geospatial,
            "measurements": measurements,
            "quality_flags": flags,
            "review": {
                "status": status,
                "reviewer_note": record.get("reviewer_note"),
                "reviewed_at": reviewed_at,
                "reviewed_by": reviewed_by if reviewed_at else None,
            },
            "provenance": {
                "source_checksum": source_checksum,
                "ingested_at": ingested_at,
                "transformations": [],
                "created_at": record["created_at"],
            },
        }
    )
