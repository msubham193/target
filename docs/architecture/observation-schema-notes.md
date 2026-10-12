# Observation Schema — Version 3.0 adoption notes

Date: 2026-10-12. Status: **documentation only.** No observation table, model or endpoint exists yet. This note records the approved direction and the Version 2.0 fields that need a decision before the schema is finalised, so that nothing is dropped silently.

Sources: the approved specification, V2 §6 (field table and example) and V3 §4 (nested contract) and §5 (states).

## 1. Approved direction

The Version 3.0 nested contract (V3 §4) is the canonical proposed schema. This settles decision D-009.

## 2. Open question: the review vocabulary

The Phase 2 approval lists the review vocabulary as `proposed`, `verified`, `inconclusive` and `disputed`.

The Version 3.0 schema it adopts lists six values: `proposed | needs_review | verified | rejected | inconclusive | disputed` (V3 §4), each defined in V3 §5.

The two omitted values carry requirements elsewhere in the specification:

| Value | Where the specification relies on it |
|---|---|
| `needs_review` | R-012 (low-confidence results are marked for review), CON-001, CON-002, REV-001 ("set status to needs_review or inconclusive"), the job state "Needs review" |
| `rejected` | R-013 (distinguish human-rejected), V3 §5 ("retain it in audit history"), the confirm/reject controls on Screens 6 and 10 |

Nothing has been implemented either way. **Before the observation schema is built, please confirm one of:**

- (a) all six V3 values are used, and the four listed were shorthand; or
- (b) only the four are used, in which case R-012, R-013, REV-001 and the reject action need a stated replacement.

Until confirmed, this is recorded as unresolved and option (a) is assumed for planning, because it changes no approved requirement.

## 3. Version 2.0 fields with no direct Version 3.0 equivalent

Each needs to be kept, mapped or explicitly dropped. The right-hand column is a proposal, not a decision.

| V2 field or rule (V2 §6) | In V3 §4? | Proposal |
|---|---|---|
| `summary` — "Must be grounded in stored fields/evidence" | No. V3 has `claim.description` ("evidence-based description") | Keep `summary` as a separate field, or state that `claim.description` replaces it. They read as the same thing; confirm. |
| `geometry_image_px` with `coordinate_space` — "Must state pixel coordinate convention" | Partly. V3 has `evidence[].region_px` ("pixel geometry or null") with no convention | **Keep the convention.** Carry `coordinate_space`, origin and pixel reference on every `region_px`. Without it a pixel box is ambiguous. |
| `geometry_image_px.type` (`bbox_xyxy`) | No | Keep as part of `region_px`. |
| `category` — "Controlled taxonomy ID/string; unknown class flagged as unclassified" | Partly. V3 has `claim.category` ("validated class or feature type") | Keep the V2 rule: an unknown class becomes `unclassified` with a flag. |
| `score` — "Number 0–1 or null; preserve raw score; document whether calibrated" | Partly. V3 has `score.raw_value`, `semantics`, `calibrated` | V3 covers it better. The 0–1 range is V2-only: keep it only as a per-model property, since not every model's raw score is in 0–1. |
| `frame_number` | Renamed `evidence[].frame_index` | Use `frame_index`. |
| `timestamp_ms` — "Integer/number" | `evidence[].timestamp_ms` — integer | Use integer milliseconds; keep the exact container timestamp and time base alongside for video. |
| `measurements` — single "Value + unit + method or null" | List of `{name, value, unit, method, validity}` | V3 covers it. |
| `evidence_refs` — "At least one source reference" | `evidence[]`, with no minimum stated | **Keep the rule**: at least one evidence entry (also required by EVD-001). |
| `evidence_refs[].region` (a type name) | `evidence[].region_px` (the geometry) | V3 covers it. |
| `quality_flags` — "Controlled codes", upper case: `UNKNOWN_CRS`, `LOW_CONFIDENCE`, `BLUR`, `METADATA_MISSING`, `SOURCE_CONFLICT`, `NOT_GEOREFERENCED` | Lower case examples: `blur`, `low_resolution`, `missing_crs`, `timestamp_unknown`, `metadata_incomplete` | Keep "controlled codes" as the rule and one registry containing every flag from both lists. `LOW_CONFIDENCE`, `SOURCE_CONFLICT` and `NOT_GEOREFERENCED` appear only in V2 and must not be lost. Phase 2 already uses upper-case codes on assets. |
| `created_at`, `reviewed_at` — "ISO timestamps; UTC in storage" | `provenance.created_at`, `review.reviewed_at`, no UTC rule | Keep the UTC rule. |
| `reviewer_note` — "Human note kept separate from model output" | `review.reviewer_note` | V3 covers the field; keep the separation rule. |
| `review_status` values `predicted`, `confirmed`, `unresolved` | `proposed`, `verified`; no equivalent of `unresolved` | `predicted → proposed`, `confirmed → verified`. `unresolved` has no exact counterpart (`inconclusive` and `disputed` are narrower). See section 2. |
| `model_name`, `model_version`, `run_id` — "Required for model-generated results" | Nested `model.{name, version, run_id}` | V3 covers the fields; keep "required for model-generated results". |
| `pipeline_id` | Renamed `pipeline` | Use `pipeline`. |

## 4. Fields only in Version 3.0

`claim.description`, `claim.claim_type` (`observed | inferred | metadata_derived | human_entered`), `evidence[].source_excerpt_or_metadata_key`, `geospatial.derivation_method`, `geospatial.uncertainty`, `geospatial.coordinate_units`, `measurements[].validity`, and the whole `provenance` block (`source_checksum`, `ingested_at`, `transformations`, `created_at`).

Phase 2 already produces the inputs for `provenance.source_checksum` and `provenance.ingested_at` (the asset's SHA-256 and ingestion time).

## 5. What Phase 2 did and did not do

Did: stored assets with checksums, metadata, unknowns and quality flags.

Did not: create any observation table, schema class, state machine or endpoint. That work waits for the answers above.
