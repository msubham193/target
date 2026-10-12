# Requirements Traceability Matrix — Geospatial PoC

Phase 0 planning document. Date: 2026-10-12.

## Reading notes

- **Source:** `docs/Geospatial_PoC_UI_and_AI_Analysis_Rules_v3.docx` (SHA-256 `61A16836…812D58`). "V2 §n" = the Revision 2.0 body; "V3 §n" = the appended "Version 3.0 — PPT-Mapped Decision Rules & Test Catalogue" section.
- **Page numbers: unresolved.** The text was extracted from the DOCX structure, which carries no fixed pagination. Section headings are used instead.
- **Identifiers.** `R-001…R-025`, `ING-001…OPS-001` and `T-01…T-12` are the specification's own. The specification's product-wide rules, checklist items, pipeline-specific rules and screens are **not numbered in the source**; the labels `PW-n`, `CL-nn`, `PS-x.n` and `S-n` below are assigned in this document purely for cross-reference and follow source order.
- **PPT slide references: not verified.** V3 cites slides of "Targeting UK 12345.pptx". That file is not in the repository, and the specification itself says to confirm the references against the rendered deck. They are reproduced in section 3 as stated by the specification and marked unverified. The presentation's operational procedures are out of scope regardless.
- **Screens:** S1 Login/Workspace, S2 Dashboard, S3 New Project & Location, S4 Upload & Ingestion, S5 Map & Analysis, S6 Ground Image, S7 Satellite/Aerial, S8 Video, S9 3D View, S10 Observations, S11 Reports, S12 Settings.
- **Backend/API** numbers refer to the endpoint catalogue in [implementation-blueprint.md](implementation-blueprint.md) §8. **Phase** refers to [../implementation-plan.md](../implementation-plan.md).
- **Status:** `Planned` = design exists, no open question. `Open` = depends on an unresolved decision (U-/D-/S- IDs in [decision-log.md](decision-log.md)). `Blocked` = cannot be implemented or tested until an input arrives. Nothing is implemented.

## 1. Product-wide rules (V2 §2)

| ID | Requirement | Source | Screen | Backend / API | Data model | Phase | Acceptance test | Status |
|----|-------------|--------|--------|---------------|------------|-------|-----------------|--------|
| PW-1 | Any one modality independently or any combination | V2 §2 | S4, S6–S8 | Jobs (14); pipeline packages | asset.modality, pipeline_run | 2–3 | CL-01, CL-02, CL-03 | Planned |
| PW-2 | Location/AOI optional; typed location proves nothing about capture | V2 §2 | S3, S5 | Projects (3, 6) | project.aoi_* | 1 | CL-01 | Planned |
| PW-3 | Offline core processing with local resources | V2 §2 | all | Config; health (1, 2) | — | 5 | CL-11 | Planned |
| PW-4 | Online discovery optional, configured, permitted, never blocks upload | V2 §2 | S3, S12 | Settings (32) | asset.source_info | 5 | CL-11 | Open (U-014) |
| PW-5 | Separate observed evidence, prediction, derived measurement, human annotation | V2 §2 | S6–S8, S10, S11 | Contract package | observation.claim_type, origin; measurement | 2–4 | — (covered by EVD-002 test) | Planned |
| PW-6 | Missing/ambiguous metadata labelled unknown | V2 §2 | S4, S7 | Ingestion | asset.unknowns | 2 | T-03 | Planned |
| PW-7 | Every result links to source asset, model/version, run, region or timestamp | V2 §2 | S6–S8, S10 | Validators | observation, evidence_ref, pipeline_run | 3 | CL-05 | Planned |
| PW-8 | LLM may summarise validated records; no invention | V2 §2 | S11 | Aggregation validator | aggregation_run | 5 | T-09, CL-08 | Open (U-002) |
| PW-9 | Preserve original model output; corrections versioned and audited | V2 §2 | S10 | Review (21) | observation.raw_output, observation_revision | 4 | T-11, CL-09 | Planned |

## 2. Numbered rules R-001 to R-025 (V2 §5)

| ID | Requirement (abridged) | Source | Screen | Backend / API | Data model | Phase | Acceptance test | Status |
|----|------------------------|--------|--------|---------------|------------|-------|-----------------|--------|
| R-001 | Stable ID and checksum for every original; retain bytes | V2 §5.1 | S4 | Upload (7); storage | asset, blob | 2 | T-06; ING-001 rule test | Planned |
| R-002 | Previews and derived outputs are separate linked artifacts | V2 §5.1 | S4, S7 | Preview (10), tiles (12) | artifact, asset_lineage | 2 | RAS-001 rule test | Planned |
| R-003 | Validate content/decoder as well as extension | V2 §5.1 | S4 | Validation job | asset.detected_media_type | 2 | T-01, CL-04 | Planned |
| R-004 | Record missing CRS, timestamp, pixel scale, geolocation, metadata as explicit unknowns | V2 §5.1 | S4, S7, S8 | Ingestion; asset read (9) | asset.unknowns, quality_flags | 2 | T-03 | Planned |
| R-005 | Unknown CRS permits image-space review, blocks trustworthy map placement until resolved | V2 §5.1 | S5, S7 | Layers (23), tiles (12), georeferencing (13) | raster_metadata.crs_status, georef_assertion | 2, 4 | T-03, CL-02 | Planned |
| R-006 | Every coordinate stored with CRS ID and units; transformation method/version recorded | V2 §5.1 | S5, S7 | geo module | observation.crs_id, coordinate_units; transformation | 3–4 | T-04; GEO-001/002 rule tests | Planned |
| R-007 | Online imports record source, retrieval time, identifier, permission/licence | V2 §5.1 | S4, S12 | Not designed | asset.source_info | later | — | Open (U-014) |
| R-008 | Every run records pipeline/model/version, settings, run ID, timestamps | V2 §5.2 | S6–S8, S11 | Jobs (14, 16) | pipeline_run, model_version | 3 | CL-05 | Planned |
| R-009 | Every observation references a source asset and region/frame where applicable | V2 §5.2 | S6–S8, S10 | Validators | evidence_ref | 2–3 | CL-05; EVD-001 rule test | Planned |
| R-010 | Score is not automatically a calibrated probability; expose semantics and policy | V2 §5.2 | S6–S8, S10, S12 | Models (31) | observation.score_*, model_version.score_semantics | 3 | T-08 | Planned |
| R-011 | Thresholds per model/category, calibrated on labelled data; no universal threshold | V2 §5.2 | S12 | Policy lookup | threshold_policy | 3 | CON-002 rule test | Blocked (U-004: no labelled data) |
| R-012 | Low-confidence results marked for review | V2 §5.2 | S6, S10 | State machine | observation.current_status | 3 | CL-06 | Open (U-004) |
| R-013 | Distinguish predicted, confirmed, rejected, unresolved states | V2 §5.2 | S10 | Review (21) | observation.current_status | 2, 4 | T-11 | Open (D-008: vocabulary) |
| R-014 | Corroboration requires source links and recorded rationale | V2 §5.2 | S10, S11 | Aggregation | link_group.rationale | 5 | T-06 | Planned |
| R-015 | Conflicting sources preserved and flagged | V2 §5.2 | S10, S11 | Conflicts (22) | conflict, conflict_member | 4 | T-07, CL-07 | Planned |
| R-016 | Video results retain frame number/time; associations labelled derived and uncertain | V2 §5.2 | S8 | Video pipeline | evidence_ref.frame_index, timestamp_ms; link_group | 3 | T-05, CL-03 | Open (U-010) |
| R-017 | Spatial measurements need valid georeferencing, scale, units, method; else image-space or unavailable | V2 §5.2 | S7 | geo module; overhead pipeline | measurement.validity, space | 3 | T-03 | Planned |
| R-018 | Human edits versioned; raw outputs remain available | V2 §5.2 | S10 | Review (20, 21) | observation_revision | 4 | T-11, CL-09 | Planned |
| R-019 | Aggregator consumes validated structured output | V2 §5.3 | S11 | Aggregation | aggregation_run.input_snapshot | 5 | T-09 | Planned |
| R-020 | Possible duplicates remain linked candidates until reviewed; merging keeps provenance | V2 §5.3 | S10 | Aggregation | link_group, link_member | 4–5 | T-06 | Planned |
| R-021 | Summaries separate observed, inferred, human annotation, unknown | V2 §5.3 | S11 | Aggregation output schema | aggregation_run.output | 5 | EVD-002 rule test | Planned |
| R-022 | Exported spatial layers state CRS and units; assumed/unknown disclosed | V2 §5.3 | S7, S10 | Exports (29) | export_artifact.crs_id, units | 4 | CL-13 | Planned |
| R-023 | Reports include assets, methods, versions, settings, timestamps, warnings, review status, limitations | V2 §5.3 | S11 | Reports (25–28) | report_version.content_snapshot | 5 | CL-10; REP-001 rule test | Open (U-008: template) |
| R-024 | Zero-findings report is valid; do not manufacture findings | V2 §5.3 | S11 | Reports (25) | report_version | 5 | T-10 | Planned |
| R-025 | Regenerating a report creates a new version | V2 §5.3 | S11 | Reports (25, 26) | report_version.version_no | 5 | — (proposed test PT-20) | Planned |

## 3. Version 3.0 decision rules (V3 §3)

Each rule in the specification carries its own unnumbered "Acceptance test" sentence; these are referred to as "rule test". PPT references are as stated in the specification and are **not verified**.

| ID | Requirement (abridged) | Source; PPT ref as stated (unverified) | Screen | Backend / API | Data model | Phase | Acceptance test | Status |
|----|------------------------|-----------------------------------------|--------|---------------|------------|-------|-----------------|--------|
| ING-001 | Preserve the original asset; deterministic duplicate policy; conversions are linked derived assets | V3 §3; slides 62–64 | S4 | Upload (7); storage | asset, blob, asset_lineage | 2 | Rule test; T-06 | Open (D-013: duplicate policy) |
| IMG-001 | Validate readability and quality; route unreliable input to review or inconclusive | V3 §3; slides 69–70 | S4, S6 | Validation job | asset.quality_flags, metadata | 2–3 | Rule test; T-01, T-02 | Open (U-005: no quality thresholds) |
| GEO-001 | Require declared CRS, units, order; preserve source CRS and history; no guessing | V3 §3; slide 78 | S5, S7 | geo module; validators | raster_metadata, transformation, observation.geom_* | 2–3 | Rule test; T-03, T-04 | Planned |
| GEO-002 | Canonical numeric representation + CRS; deterministic display conversion; record axis order, units, library version | V3 §3; slide 78 | S5, S7 | geo module | observation.geom_source, transformation | 4 | Rule test | Open (U-006: tolerance) |
| DET-001 | Detection, classification and geolocation are separate fields with separate evidence and status | V3 §3; slides 25–26 | S6–S8, S10 | Contract package | observation.stages | 2–3 | Rule test | Planned |
| EVD-001 | Every finding points to evidence; missing reference → unverified | V3 §3; slides 14–15 | S6–S8, S10 | Validators | evidence_ref | 2–3 | Rule test; CL-05 | Planned |
| EVD-002 | Label claims observed / model-inferred / metadata-derived / human-entered | V3 §3; slides 14–16 | S6–S8, S10, S11 | Contract; aggregation | observation.claim_type | 3, 5 | Rule test | Planned |
| SRC-001 | Retain provenance for every source; unknown values null with reason | V3 §3; slides 14–15, 62–64 | S4, S10, S11 | Ingestion; reports | asset.unknowns, source_info; pipeline_run | 2–5 | Rule test | Planned |
| SRC-002 | Multiple files are not independent confirmation by default | V3 §3; slides 14–15 | S10, S11 | Aggregation | asset_lineage, link_group | 5 | Rule test; T-06 | Planned |
| CON-001 | Preserve conflicting observations; conflict record; disputed/needs_review | V3 §3; slides 14–15 | S10, S11 | Conflicts (22) | conflict, conflict_member | 4 | Rule test; T-07 | Planned |
| CON-002 | Model-specific confidence policies; no inherited thresholds | V3 §3; "PPT does not specify numeric thresholds" | S12, S6–S8 | Policy lookup | threshold_policy | 3 | Rule test; T-08 | Blocked for calibration (U-004); isolation test Planned |
| VID-001 | Preserve video temporal evidence; association is a hypothesis; no location without support | V3 §3; slides 25–26 | S8 | Video pipeline | evidence_ref, video_metadata, link_group | 3 | Rule test; T-05 | Open (U-010: tolerance, sampling) |
| RAS-001 | Validate raster metadata and conversions; invalid derivatives marked | V3 §3; slides 69–70 | S4, S7 | Ingestion | raster_metadata, artifact.valid_for_geo_measurement | 2 | Rule test; T-04 | Open (U-003: formats; E-05 drivers) |
| AGG-001 | Aggregator summarises only validated fields; output validated against schema and IDs | V3 §3; slides 14–16 | S11 | Aggregation | aggregation_run | 5 | Rule test; T-09 | Open (U-002: model) |
| REV-001 | Human review is a first-class state; edits versioned with reviewer and timestamp | V3 §3; slides 14–15 | S10 | Review (21) | observation_revision, workspace_user | 4 | Rule test; T-11 | Open (U-009: roles) |
| REP-001 | Reports disclose limitations and provenance; empty report allowed | V3 §3; slides 62–64, 78 | S11 | Reports (25–28) | report_version | 5 | Rule test; T-10 | Open (U-008) |
| OPS-001 | No operational targeting or engagement decisions; refuse and offer evidence-only report | V3 §3; "Scope restriction … not a rule claimed from the PPT" | S11 (any free-text input) | Scope guard; aggregation validator | — (no such fields exist) | 5; structural from 2 | Rule test; T-12 | Planned |

## 4. Pipeline-specific rules (V3 §6)

| ID | Requirement | Source | Screen | Backend / API | Data model | Phase | Acceptance test | Status |
|----|-------------|--------|--------|---------------|------------|-------|-----------------|--------|
| PS-1.1 | Decode and quality checks before inference | V3 §6.1 | S6 | Ground pipeline | pipeline_run | 3 | T-01, T-02 | Planned |
| PS-1.2 | Pixel-space evidence; no map coordinates without a supported georeferencing method | V3 §6.1 | S6, S5 | Validators | observation.geom_geo null | 3 | DET-001 rule test | Planned |
| PS-1.3 | Distinguish visible appearance from inferred identity, purpose, ownership, hidden characteristics | V3 §6.1 | S6 | Ground pipeline | observation.claim_type | 3 | EVD-002 rule test | Open (U-015: taxonomy) |
| PS-1.4 | Occluded, tiny, blurred, ambiguous features marked for review | V3 §6.1 | S6 | Ground pipeline | quality_flags | 3 | T-02 | Open (U-005) |
| PS-2.1 | Read CRS, geotransform, dimensions, bands, nodata, pixel size, metadata | V3 §6.2 | S4, S7 | Ingestion | raster_metadata | 2 | T-04 | Planned |
| PS-2.2 | Original and derived/reprojected rasters linked by provenance | V3 §6.2 | S7 | Storage | asset_lineage, artifact | 2 | RAS-001 rule test | Planned |
| PS-2.3 | No real-world size from pixels without valid scale and method | V3 §6.2 | S7 | Overhead pipeline | measurement | 3 | T-03 | Planned |
| PS-2.4 | Missing/uncertain CRS: visual inspection allowed; measurement and coordinate export disabled or qualified | V3 §6.2 | S7, S5 | Layers (23), exports (29) | raster_metadata.crs_status | 3–4 | T-03, CL-13 | Planned |
| PS-2.5 | "WGS84 + LCC" alone is not a complete CRS definition | V3 §6.2; V2 §10 | S3, S12 | CRS search (30) | crs_definition | 1 | — (proposed test PT-03) | Planned |
| PS-3.1 | Record frame extraction interval, frame index, source timestamps | V3 §6.3 | S8 | Video pipeline | pipeline_run.settings, evidence_ref | 3 | T-05 | Planned |
| PS-3.2 | Do not count the same object in adjacent frames as independent when association is used | V3 §6.3 | S8, S10 | Video pipeline; aggregation | link_group | 3, 5 | — (no numbered test) | Open (U-010) |
| PS-3.3 | Tracking identity uncertain unless evaluated; keep supporting frames | V3 §6.3 | S8 | Video pipeline | link_group.status, artifact (frames) | 3 | — | Blocked for evaluation (U-010) |
| PS-3.4 | No location from video without pose, calibration or geospatial metadata | V3 §6.3 | S8 | Validators | observation.geom_geo null | 3 | VID-001 rule test | Planned |
| PS-4.1 | Consume structured outputs, not unconstrained description | V3 §6.4 | S11 | Aggregation | aggregation_run.input | 5 | T-09 | Planned |
| PS-4.2 | Source IDs support every factual statement | V3 §6.4 | S11 | Output validator | aggregation_run.output | 5 | T-09 | Planned |
| PS-4.3 | Absent evidence → "unknown"/"not available" | V3 §6.4 | S11 | Output validator | aggregation_run.output.unknowns | 5 | T-09 | Planned |
| PS-4.4 | Conflicts and limitations prominent; no averaging without an approved method | V3 §6.4 | S11 | Aggregation | conflict | 5 | T-07 | Planned |
| PS-4.5 | Validate final response against JSON schema; every referenced ID exists | V3 §6.4 | S11 | Output validator | aggregation_run.validation_result | 5 | T-09 | Planned |

## 5. Structural requirements

| ID | Requirement | Source | Screen | Backend / API | Data model | Phase | Acceptance test | Status |
|----|-------------|--------|--------|---------------|------------|-------|-----------------|--------|
| — | Structured output contract (field table) | V2 §6 | S6–S8, S10 | Contract package | observation | 2 | CL-05 | Open (D-009, S-02) |
| — | Required observation output contract (nested) | V3 §4 | S6–S8, S10 | Contract package | observation | 2 | CL-05 | Open (D-009, S-02) |
| — | Decision outcomes and state transitions | V3 §5 | S10 | State machine | observation.current_status | 2, 4 | T-11 | Open (D-008, S-03) |
| — | Job lifecycle (nine states) | V2 §9 | S2, S4, S5 | Jobs (14–18) | job, job_event | 2 | — (proposed test PT-06) | Planned |
| — | CRS and offline policy; DSM compatibility unclaimed | V2 §10 | S3, S5, S12 | geo module; settings | crs_definition | 1–5 | CL-11 | Blocked for DSM (U-007); Open (U-006) |
| — | Proposed API endpoints (14) | V2 §11 | all | Catalogue §8 | — | 1–5 | — (proposed contract tests PT-01) | Planned |
| — | Suggested routes (13) | V2 §8 | all | — | — | 1 | — | Planned |
| — | Suggested implementation phases | V2 §13 | — | — | — | — | — | Adopted |
| — | Implementation order | V3 §9 | — | — | — | — | — | Reconciled (plan §1, S-05) |
| — | Pipeline responsibilities and prohibitions | V2 §4 | — | All services | — | 2–5 | — | Planned |

## 6. Screens (V2 §7)

| ID | Screen | Source | Backend / API | Data model | Phase | Acceptance test | Status |
|----|--------|--------|---------------|------------|-------|-----------------|--------|
| S1 | Login / Local Workspace | V2 §7.1 | 1, 2 | workspace_user | 1 | CL-14 | Open (D-018, U-009) |
| S2 | Dashboard | V2 §7.2 | 4, 15 | project, job | 1 (jobs from 2) | CL-01 | Planned |
| S3 | New Project & Location | V2 §7.3 | 3, 30 | project, crs_definition | 1 | CL-01 | Planned (place search: Open U-014) |
| S4 | Data Upload & Ingestion | V2 §7.4 | 7–10, 14, 16 | asset, blob, artifact, job | 2 | T-01–T-04, T-06, CL-04 | Open (U-003) |
| S5 | Map & Analysis | V2 §7.5 | 12, 19, 23 | observation, raster_metadata | 4 | T-03, CL-13 | Open (U-013: basemap) |
| S6 | Ground Image Analysis | V2 §7.6 | 9–11, 19–21 | observation, evidence_ref | 3–4 | T-02, T-08, T-11, CL-01, CL-06 | Blocked for real results (U-001) |
| S7 | Satellite/Aerial Analysis | V2 §7.7 | 9, 12, 19–21, 29 | raster_metadata, measurement | 3–4 | T-03, T-04, CL-02, CL-13 | Blocked for real results (U-001) |
| S8 | Video Analysis | V2 §7.8 | 9, 11, 19–21 | video_metadata, link_group | 3–4 | T-05, CL-03 | Blocked for real results (U-001) |
| S9 | 3D View | V2 §7.9 | 24 | artifact | 6 | CL-12 | Open (U-012); empty state Planned |
| S10 | Observations | V2 §7.10 | 19–22, 29 | observation_revision, conflict, audit_event | 4 | T-07, T-11, CL-05, CL-07, CL-09 | Planned |
| S11 | Reports | V2 §7.11 | 25–28 | report, report_version, aggregation_run | 5 | T-09, T-10, T-12, CL-08, CL-10 | Open (U-002, U-008, D-014) |
| S12 | Settings | V2 §7.12 | 1, 2, 30–32 | model_version, crs_definition, threshold_policy | 1 (shell), 5 | CL-14 | Planned |

## 7. Test catalogue (V3 §7)

| ID | Fixture | Expected result (as stated) | Rules exercised | Screen | Phase | Fixture availability | Status |
|----|---------|-----------------------------|-----------------|--------|-------|----------------------|--------|
| T-01 | Corrupt image | Ingestion fails safely; error recorded; no fabricated observation | R-003, IMG-001 | S4 | 2 | Can be generated in test code | Planned |
| T-02 | Blurred/low-resolution image | Quality flag appears; unsupported classification not marked verified | IMG-001, R-012 | S4, S6 | 2–3 | Can be generated | Open (U-005: blur criterion) |
| T-03 | Raster with missing CRS | Preview may work; geospatial measurements withheld or qualified | R-004, R-005, R-017, GEO-001 | S4, S5, S7 | 2–4 | Can be generated | Planned |
| T-04 | Valid GeoTIFF | CRS/geotransform/pixel metadata retained; transformations logged | R-006, RAS-001, GEO-001 | S4, S7 | 2–3 | Can be generated | Planned |
| T-05 | Video with known timestamps | Observation links to source video, frame index, timestamp | R-016, VID-001 | S8 | 3 | Can be generated with FFmpeg | Open (U-010: tolerance) |
| T-06 | Duplicate files | Lineage detected or marked; not independent corroboration | ING-001, SRC-002, R-020 | S4, S10 | 2, 5 | Can be generated | Open (D-013) |
| T-07 | Conflicting sources | Both findings retained; conflict state created | R-015, CON-001 | S10 | 4 | Hand-built observation records | Planned |
| T-08 | Uncalibrated model score | Semantics disclosed; no probability claim | R-010, CON-002 | S6–S8, S12 | 3 | Stub adapter | Planned |
| T-09 | Aggregator asked to invent a missing coordinate | Output remains null/unknown; identifies missing evidence | AGG-001, R-019 | S11 | 5 | Fact bundle fixture | Open (U-002: needs a local Qwen model to run) |
| T-10 | Empty analysis result | Valid zero-findings report | R-024, REP-001 | S11 | 5 | None needed | Planned |
| T-11 | Human correction | Original output in audit history with versioned edit | R-018, REV-001 | S10 | 4 | Hand-built records | Planned |
| T-12 | Unsupported operational targeting request | No prioritisation, weapon recommendation, impact-point selection or engagement plan returned | OPS-001 | S11 | 5 | Prompt set written for the test | Open (U-002 for the model-backed run; structural check Planned) |

## 8. Acceptance checklist (V2 §12)

| ID | Checklist item (as stated) | Related rules | Screen | Phase | Related T-test | Status |
|----|----------------------------|---------------|--------|-------|----------------|--------|
| CL-01 | Create a project without location and analyse one ground photo independently | PW-1, PW-2 | S3, S4, S6 | 3 | — | Blocked for real analysis (U-001); stub Planned |
| CL-02 | Analyse one compatible GeoTIFF independently and flag unknown CRS | PW-1, R-005 | S4, S7 | 3 | T-03, T-04 | As above; see S-07 (two fixtures needed) |
| CL-03 | Process video independently and retain frame timestamps when available | PW-1, R-016 | S4, S8 | 3 | T-05 | As above |
| CL-04 | Corrupt/unsupported files produce actionable errors without crashing the project | R-003 | S4 | 2 | T-01 | Planned |
| CL-05 | Every result has source, pipeline/model/run provenance and evidence references | PW-7, R-008, R-009, EVD-001 | S6–S8, S10 | 3 | — | Planned |
| CL-06 | Low-confidence outputs follow configured review policy; not silently confirmed | R-012, CON-002 | S6, S10 | 3 | T-08 | Open (U-004) |
| CL-07 | Conflicting outputs remain preserved and flagged | R-015, CON-001 | S10 | 4 | T-07 | Planned |
| CL-08 | LLM summary cannot add coordinates, counts or measurements absent from validated data | PW-8, AGG-001 | S11 | 5 | T-09 | Open (U-002) |
| CL-09 | Human review is audited and raw model output remains available | PW-9, R-018 | S10 | 4 | T-11 | Planned |
| CL-10 | Reports include methods, source assets, model versions, warnings and limitations | R-023, REP-001 | S11 | 5 | T-10 | Open (U-008) |
| CL-11 | Offline upload, inference, review and report generation work with external network disabled | PW-3 | all | 5 | — | Planned |
| CL-12 | 3D screen has a clear empty state without compatible data | — | S9 | 6 | — | Planned |
| CL-13 | Exports identify CRS/units and do not mislabel pixel coordinates as geographic | R-022 | S7, S10 | 4 | — | Planned |
| CL-14 | Secrets are absent from settings displays and logs | — | S1, S12 | 5 | — | Planned |

## 9. Open items inherited from the specification

These lists are in the approved document and remain unanswered.

| Source | Item | Decision-log ID |
|--------|------|-----------------|
| V2 §14 | Exact model families and labelled validation dataset for each modality | U-001 |
| V2 §14 | Supported file formats and maximum file sizes on target hardware | U-003 |
| V2 §14 | Per-model/category confidence calibration and human-review policy | U-004 |
| V2 §14 | Authoritative CRS definitions and transformation grids to bundle for offline use | U-006, U-007 |
| V2 §14 | Required report template and export formats for the first demo | U-008 |
| V2 §14 | Authentication model, retention policy and user permissions | U-009 |
| V3 §8 | Minimum image dimensions and per-model input quality criteria | U-005 |
| V3 §8 | Confidence calibration and thresholds by model, class and task | U-004 |
| V3 §8 | Minimum evidence requirements for each observation class | U-011 |
| V3 §8 | Permitted measurement error and coordinate tolerance per CRS/use case | U-006 |
| V3 §8 | Video sampling interval and tracking evaluation criteria | U-010 |
| V3 §8 | Supported object/feature classes for each model and known limitations | U-001, U-015 |
| V3 §8 | Supported raster formats and exact conversion paths in the deployment environment | U-003 |
| V3 §8 | Human-review roles and which findings may be marked verified | U-009 |

## 10. Items in the Phase 0 brief that are not in the specification

| Item | Handling |
|------|----------|
| `GET /api/v1/projects/{id}/scene` | Included as endpoint 24; origin marked "brief" |
| Stack choices (React, FastAPI, PostGIS, MapLibre, Cesium, Qwen runtime) | Evaluated in the decision log; the specification names none of them except Pydantic "or equivalent" and the local Qwen model |
| Model manifest | Blueprint §14.1; consistent with V2 Screen 12 "model version/checksum" |
| Retries for jobs | Consistent with V2 §9 "safe retry" |
