# Canonical Observation Schema

Date: 2026-10-12. Status: **agreed and implemented as a validated contract** in `services/api/geopoc/contracts/`. Supersedes the open questions in [observation-schema-notes.md](observation-schema-notes.md).

Basis: the Version 3.0 nested contract (specification V3 §4), with the Version 2.0 rules (V2 §6) that must survive. Every observation is validated against this contract before it is stored; a record that fails is not stored as an observation and the failure is recorded on the run.

Null means genuinely unknown. The contract never derives a value.

## 1. Structure

```jsonc
{
  "schema_version": "3.0",
  "observation_id": "uuid",
  "project_id": "uuid",
  "asset_id": "uuid",                       // the source asset
  "pipeline": "ground_image | overhead_raster | video",
  "model": { "name": "...", "version": "...", "run_id": "uuid" },   // null only for human_entered claims
  "claim": {
    "category": "taxonomy id, e.g. coco:person, or 'unclassified'",
    "description": "evidence-based text",
    "claim_type": "observed | inferred | metadata_derived | human_entered"
  },
  "score": {
    "raw_value": 0.82,                      // number or null; the model's own value, unchanged
    "semantics": "what the number means for this model",
    "calibrated": false,
    "value_range": [0, 1]                   // stated per model; null if the model has no fixed range
  },
  "evidence": [                             // at least one
    {
      "asset_id": "uuid",
      "region_px": {
        "type": "bbox_xyxy | polygon",
        "coordinates": [120, 80, 420, 310],
        "coordinate_space": "source_image_pixels",
        "origin": "top_left",
        "pixel_reference": "corner",
        "image_width": 640,
        "image_height": 480
      },
      "frame_index": null,
      "timestamp_ms": null,
      "source_excerpt_or_metadata_key": null
    }
  ],
  "geospatial": {
    "geometry": null,                       // GeoJSON, or null
    "crs_id": null,                         // complete authority code, e.g. EPSG:32643
    "coordinate_units": null,
    "derivation_method": null,              // raster_affine_transform | human_entered
    "uncertainty": null                     // { value, unit }
  },
  "measurements": [
    { "name": "...", "value": 12.5, "unit": "px", "method": "...", "validity": "valid | estimated | unavailable" }
  ],
  "quality_flags": ["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE"],
  "review": {
    "status": "proposed | needs_review | verified | inconclusive | disputed | rejected",
    "reviewer_note": null,
    "reviewed_at": null,
    "reviewed_by": null
  },
  "provenance": {
    "source_checksum": "sha256 of the source asset",
    "ingested_at": "UTC timestamp",
    "transformations": [],
    "created_at": "UTC timestamp"
  }
}
```

Unknown fields are refused, not ignored.

## 2. Pixel-coordinate convention

Every image region states its convention, because a box of four numbers is otherwise ambiguous.

- **Space:** `source_image_pixels` — the pixel grid of the stored original exactly as decoded. EXIF orientation is **not** applied. Previews follow the same rule, so overlays line up.
- **Origin:** the top-left corner of the top-left pixel. x increases to the right, y increases downwards.
- **Pixel reference:** `corner`. Coordinates are continuous; pixel (0, 0) covers [0, 1) × [0, 1). A box covering the whole image is `[0, 0, width, height]`.
- **Box order:** `bbox_xyxy` = `[x_min, y_min, x_max, y_max]`, with `x_min < x_max` and `y_min < y_max`.
- **Frame of reference:** `image_width` and `image_height` of the image the coordinates refer to are stored with the region, and every coordinate must lie within it.

If inference runs on a downscaled copy, the pipeline converts coordinates back to this space before the observation is built and records the scale it used in the run settings.

## 3. Pixels never become geographic coordinates

- `geospatial.geometry` is all-or-nothing: with a geometry, `crs_id`, `coordinate_units` and `derivation_method` are all required; without one, they must all be null.
- `crs_id` must be a complete authority code. "WGS84", "WGS84 + LCC" and bare numbers are refused.
- `derivation_method` is one of `raster_affine_transform` (a georeferenced raster's own transform) or `human_entered` (which also requires a `human_entered` claim). There is no method that turns an image position into a place.
- A `ground_image` or `video` observation **cannot** carry a geometry at all: no supported georeferencing method exists for them.
- An observation without a geometry must carry `NOT_GEOREFERENCED`; one with a geometry must not.

## 4. Evidence rules

- At least one evidence entry (V2 rule; EVD-001).
- At least one entry must reference the observation's own source asset.
- A model finding in a ground photograph must point to an image region.
- A video observation needs a frame index or timestamp, or the `TIMESTAMP_UNKNOWN` flag.
- `frame_index` and `timestamp_ms` are non-negative integers.

## 5. Review states

All six Version 3.0 states are used. They keep four situations apart:

| Situation | State | Meaning (V3 §5) |
|---|---|---|
| — | `proposed` | Evidence exists and passes technical validation. Not human verification. |
| Pending review | `needs_review` | Quality, confidence, provenance or a conflict requires human inspection. Nothing is concluded. |
| Uncertainty | `inconclusive` | The available input cannot support the claim. |
| Disagreement | `disputed` | Supported observations conflict and no documented rule resolves it. |
| — | `verified` | A reviewer or an approved validation process confirms it. Who or what is recorded. |
| Rejection | `rejected` | Established to be invalid. Kept in the audit history. |

### Permitted transitions

Rows are the current state. R = a reviewer may; S = the system may; **\*** = a reason must be recorded. Blank = not permitted.

| from \ to | proposed | needs_review | verified | inconclusive | disputed | rejected |
|---|---|---|---|---|---|---|
| **proposed** | | R, S | R | R\*, S\* | S | R\*, S\* |
| **needs_review** | | | R | R\* | S | R\* |
| **inconclusive** | | R\*, S\* | | | S | R\* |
| **disputed** | | R\*, S\* | R\* | | | R\* |
| **verified** | | R\* | | | S | |
| **rejected** | | R\* | | | | |

Eighteen transitions are permitted; every other pair is refused. The rules behind the table:

1. **Only a reviewer verifies.** The system can never set `verified`. No approved automatic validation process exists yet.
2. **Who or what made every change is recorded**, with a timestamp.
3. **Rejection always records a reason**, and a rejected observation is kept. It can only be reopened to `needs_review`.
4. **Uncertainty is not asserted away.** `inconclusive` cannot go straight to `verified`; it returns to review first.
5. **Disagreement is raised only by conflict detection**, and leaving `disputed` always needs a documented resolution. Only a reviewer may conclude a dispute.
6. **A concluded finding is reopened, never flipped.** `verified` ↔ `rejected` is not allowed directly.
7. **Nothing returns to `proposed`.** Once a concern has been raised it is resolved by review.
8. No transition deletes anything.

### Starting state

Set by policy, never by the model:

| Condition | Starting state |
|---|---|
| Member of an open conflict | `disputed` |
| Input cannot support the claim | `inconclusive` |
| Score uncalibrated, or below a calibrated threshold, or a review-forcing flag is present | `needs_review` |
| Otherwise | `proposed` |

No model has a calibrated threshold yet, so every model observation currently starts as `needs_review`. A model never starts an observation as `verified` or `rejected`.

### Rules on the stored review block

- `verified` and `rejected` require `reviewed_at` and `reviewed_by`.
- `rejected` requires `reviewer_note`.
- `proposed` must have no reviewer fields.

## 6. Other rules

- **Model identity** is required unless the claim is `human_entered`.
- **Score:** finite or null; inside `value_range` when one is stated; a calibrated score must have a value; an uncalibrated model score must carry `UNCALIBRATED_SCORE`.
- **Category:** a taxonomy id; anything outside the taxonomy becomes `unclassified` with the `UNCLASSIFIED` flag.
- **Measurements:** `valid` and `estimated` need a finite value, a unit and a method; `unavailable` has no value.
- **Timestamps** must state their UTC offset and are stored in UTC.
- **Quality flags** come from one controlled registry and may not repeat.

### Quality-flag registry

Canonical codes are upper case. The lower-case examples in V3 §4 and the V2 synonym `UNKNOWN_CRS` (→ `MISSING_CRS`) are accepted on input and normalised.

From the specification: `MISSING_CRS`, `LOW_CONFIDENCE`, `BLUR`, `LOW_RESOLUTION`, `METADATA_MISSING`, `METADATA_INCOMPLETE`, `SOURCE_CONFLICT`, `NOT_GEOREFERENCED`, `TIMESTAMP_UNKNOWN`.

Added by this project: `INVALID_CRS`, `UNCALIBRATED_SCORE`, `UNCLASSIFIED`, `EVIDENCE_INCOMPLETE`, `DUPLICATE_ASSET`, `OCCLUDED`, `PARTIAL_OUTPUT`.

## 7. Version 2.0 fields: retained, mapped or dropped

All 21 fields of the V2 field table (V2 §6) are accounted for. **None is dropped.**

| V2 field | Disposition | V3 location | Notes |
|---|---|---|---|
| `observation_id` | Retained | `observation_id` | |
| `project_id` | Retained | `project_id` | |
| `asset_id` | Retained | `asset_id` | |
| `pipeline_id` | Mapped | `pipeline` | Renamed |
| `model_name` | Mapped | `model.name` | "Required for model-generated results" kept |
| `model_version` | Mapped | `model.version` | |
| `run_id` | Mapped | `model.run_id` | |
| `category` | Mapped | `claim.category` | "Unknown class flagged as unclassified" kept |
| `score` | Mapped | `score.raw_value` | The V2 "0–1" rule becomes a per-model `value_range`; V2 records convert with range [0, 1] |
| `review_status` | Mapped | `review.status` | See below |
| `geometry_image_px` | Mapped | `evidence[].region_px` | **Pixel convention kept** on every region |
| `geometry_geo` | Mapped | `geospatial` | Units and method must be supplied; never assumed |
| `frame_number` | Mapped | `evidence[].frame_index` | Renamed |
| `timestamp_ms` | Mapped | `evidence[].timestamp_ms` | Whole milliseconds |
| `measurements` | Mapped | `measurements[]` | Single value becomes a one-item list |
| `evidence_refs` | Mapped | `evidence[]` | **"At least one source reference" kept** |
| `quality_flags` | Mapped | `quality_flags` | Normalised through the registry |
| `summary` | Mapped | `claim.description` | Same purpose ("grounded in stored fields/evidence"); one field instead of two |
| `created_at` | Mapped | `provenance.created_at` | "UTC in storage" kept |
| `reviewed_at` | Mapped | `review.reviewed_at` | "UTC in storage" kept |
| `reviewer_note` | Mapped | `review.reviewer_note` | "Kept separate from model output" kept |

V2 review states map as: `predicted → proposed`, `needs_review → needs_review`, `confirmed → verified`, `rejected → rejected`, `unresolved → needs_review`. V2 `unresolved` meant "not yet settled by review"; V3 `inconclusive` and `disputed` are narrower, so pending review is the faithful target.

Two mappings were judgement calls and were **accepted by the project owner on 2026-10-12** (decision log G-01, G-02):

- `summary` → `claim.description`. The original text is carried across unchanged; nothing is lost.
- `unresolved` → `needs_review`. Semantic mapping: "not yet settled by review" becomes "pending review". It is not mapped to `inconclusive` (which asserts the input cannot support the claim) or `disputed` (which asserts a conflict between supported observations), because a V2 `unresolved` record asserts neither.

A converter (`contracts/v2.py`) turns a V2 record into the canonical form and refuses fields that are not in the V2 contract rather than ignoring them. A V2 record lacks things V3 requires (source checksum, ingestion time, score semantics, claim type), so the caller supplies them.

### Added relative to V3 §4

Small additions needed to make the V3 contract enforceable: `schema_version`; `score.value_range`; the convention fields on `region_px`; `review.reviewed_by` (V3 §5 requires recording who verified).

## 8. What is implemented

- The contract, the flag registry, the state rules and the V2 converter, with schema-validation tests.
- Storage of validated observations produced by the ground-image pipeline.

Not implemented: the review API and revision history (changing an observation's state through the application), conflict detection, and geographic geometry on stored observations. Those belong to Phase 4.
