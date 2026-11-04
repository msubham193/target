# Decision Log — Geospatial PoC (Phase 0)

Date: 2026-10-12. Status of every entry: **proposed**. Nothing here has been implemented.

"Spec" means `docs/Geospatial_PoC_UI_and_AI_Analysis_Rules_v3.docx`. "V2 §n" refers to the Revision 2.0 body of that document, "V3 §n" to its appended "Version 3.0 — PPT-Mapped Decision Rules & Test Catalogue" section.

Approval column: **Yes** = must be approved before the phase shown; **No** = engineering default, can be changed later at low cost.

## Part A — Proposed decisions

### D-001 Modular monolith: one API process, one worker process, one codebase

- **Context:** Single-machine PoC, one developer, three pipelines that must stay independent.
- **Decision:** One Python package tree. Process 1 serves FastAPI. Process 2 is a worker that claims jobs from the database. Both import the same domain modules.
- **Alternatives:** (a) Everything in the API process with background threads; (b) one microservice per pipeline.
- **Trade-offs:** (a) is simpler but GPU work would block or crash the API and cannot be cancelled cleanly. (b) adds deployment and contract overhead with no PoC benefit.
- **Evidence/assumptions:** Brief asks for a modular monolith; 8 GB VRAM forces GPU work to be serialised anyway.
- **Approval required:** No.

### D-002 Job queue in PostgreSQL, no broker

- **Context:** Spec V2 §9 defines a job lifecycle with cancel and "safe retry". Brief says not to introduce distributed task processing without a clear requirement.
- **Decision:** A `job` table claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, worker heartbeat, stale-job recovery, bounded retries, cooperative cancellation. Each pipeline stage runs in a child process so it can be terminated and so GPU memory is released on exit.
- **Alternatives:** Celery or RQ with Redis; Dramatiq; Windows-native schedulers.
- **Trade-offs:** No broker to install or keep running; job state is transactional with results. Throughput is low, which is acceptable for one machine and one GPU.
- **Evidence/assumptions:** Redis containers on this machine belong to other projects. Celery's prefork pool is not supported on Windows.
- **Approval required:** No.

### D-003 PostgreSQL + PostGIS in Docker Compose on host port 5433

- **Context:** No native PostgreSQL. Docker Desktop is installed and running. Port 5432 is used by another project. No PostGIS image is present locally.
- **Decision:** `postgis/postgis` image, named volume, host port 5433, exact image tag pinned at Phase 1 after checking what is published.
- **Alternatives:** Native PostgreSQL + PostGIS via the Windows installer/StackBuilder; SQLite + SpatiaLite.
- **Trade-offs:** Docker is reproducible and removable but requires Docker Desktop to be running and needs disk space where Docker keeps its image store. Native install avoids Docker but changes the machine. SpatiaLite departs from the requested baseline.
- **Evidence/assumptions:** Environment assessment §2.6–2.7. Requires an image download.
- **Approval required:** **Yes — before Phase 1.**

### D-004 Content-addressed filesystem storage outside the repository

- **Context:** R-001/R-002/ING-001 require originals to be retained byte-for-byte with derived artifacts stored separately. C: has 8.4 GB free.
- **Decision:** A configurable `DATA_ROOT` on D:. Originals stored once per SHA-256 under `originals/`, written once and then set read-only. Derived artifacts, previews, frames, reports, tile caches and temp files live in separate subtrees. The database holds paths relative to `DATA_ROOT`.
- **Alternatives:** Blobs in PostgreSQL; MinIO/S3-compatible object store.
- **Trade-offs:** Filesystem is simplest and fast for GDAL/FFmpeg, which need real paths. No built-in replication, which a PoC does not need.
- **Approval required:** **Yes (location of `DATA_ROOT`) — before Phase 1.**

### D-005 Python environment managed by uv; Python 3.11

- **Context:** Python 3.11.9 and uv 0.10.7 are installed. The global interpreter holds unrelated packages, including a CPU-only PyTorch.
- **Decision:** Project-local `.venv` created by uv, one `pyproject.toml` workspace, committed `uv.lock`. Start on 3.11 because it is present; move only if a required wheel demands it.
- **Alternatives:** conda/mamba (not installed; easiest GDAL on Windows); pip + venv; Python 3.12/3.13.
- **Trade-offs:** uv is fast and reproducible. Relying on pip wheels for GDAL means accepting the drivers those wheels ship (risk E-05).
- **Approval required:** No.

### D-006 Frontend stack as proposed, plus routing, server-state and a generated API client

- **Context:** Brief proposes React, TypeScript, Vite, Tailwind, MapLibre GL JS, CesiumJS. Node 24.14.0 and pnpm 10.33.0 are installed.
- **Decision:** Accept the baseline. Add React Router (spec V2 §8 routes), TanStack Query (polling job state, caching), and a TypeScript client generated from the FastAPI OpenAPI document. CesiumJS is lazy-loaded only on the 3D route.
- **Alternatives:** Next.js; Redux; hand-written fetch layer; deck.gl instead of Cesium.
- **Trade-offs:** Generated client keeps frontend and backend contracts in step. Lazy-loading Cesium keeps the core bundle small and keeps 3D optional.
- **Approval required:** No.

### D-007 Local LLM runtime: Ollama over localhost HTTP

- **Context:** Brief requires a local, configurable Qwen model. Ollama 0.40.2 is installed and listening on 127.0.0.1:11434 with Qwen-family models already present.
- **Decision:** The aggregator calls Ollama through a thin `LlmClient` interface, requesting schema-constrained JSON output. The model tag and digest come from the model manifest. Tags that are cloud-routed are rejected by the manifest loader.
- **Alternatives:** llama.cpp server; Hugging Face Transformers in-process; vLLM (no native Windows support).
- **Trade-offs:** Ollama is already installed and manages GPU/CPU offload and unloading. It is a separate process whose availability must be health-checked. Running the LLM in-process would compete with vision models for VRAM inside the worker.
- **Evidence/assumptions:** Environment assessment §2.5. No model has been run or benchmarked.
- **Approval required:** No for the runtime; the **model** is U-002.

### D-008 Canonical review vocabulary and quality-flag codes

- **Context:** The spec contains two observation schemas. V2 §6 uses `predicted / needs_review / confirmed / rejected / unresolved` and upper-case flags (`UNKNOWN_CRS`). V3 §4–5 uses `proposed / needs_review / verified / rejected / inconclusive / disputed` and lower-case flags (`missing_crs`). They cannot both be implemented literally.
- **Decision:** Adopt the V3 six-state vocabulary as canonical, because V3 states it supplements V2 and defines each state's meaning. Store flags as upper-case snake-case codes from one registry that contains every flag named in either section.
- **Alternatives:** Keep the V2 five states; support both and translate at the API edge.
- **Trade-offs:** One vocabulary avoids ambiguity. The mapping `predicted→proposed` and `confirmed→verified` is direct; V2 `unresolved` has no exact V3 equivalent (`inconclusive` and `disputed` are both narrower).
- **Approval required:** **Yes — before Phase 2** (it fixes the schema).

### D-009 Observation schema: V3 nested structure, with V2 fields preserved

- **Context:** Same duplication as D-008, for field layout (`pipeline_id` vs `pipeline`, flat `model_name` vs nested `model`, single `geometry_image_px` vs per-evidence `region_px`).
- **Decision:** Use the V3 §4 nested contract as the base. Keep V2-only fields: `summary`, pixel `coordinate_space` convention, explicit `category` taxonomy rule. Detection, classification and geolocation are separate sub-objects with separate status (DET-001). Full proposal in the blueprint §9.
- **Alternatives:** V2 flat schema; two schemas.
- **Approval required:** **Yes — before Phase 2.**

### D-010 Raw model output is immutable; review is an append-only revision history

- **Context:** R-018, REV-001, T-11: raw output must remain available; human edits are versioned.
- **Decision:** `observation` rows are never updated after creation except for denormalised "current state" columns. Every review action inserts an `observation_revision` row. The API returns both the original and the current effective values.
- **Alternatives:** Update in place plus a generic audit log; temporal tables.
- **Trade-offs:** Slightly more complex reads; no possibility of losing the original.
- **Approval required:** No.

### D-011 CRS handling: PROJ/EPSG registry, authority-coded identifiers, no inference

- **Context:** V2 §10, R-004–R-006, GEO-001/002, brief §9.
- **Decision:** A CRS is accepted only as an authority code resolvable by the bundled PROJ database, or as a complete WKT2/PROJJSON definition registered explicitly in Settings with a recorded source. Geometries keep their source CRS and source coordinates; a second copy in EPSG:4326 is stored for map display together with a transformation record. All internal arrays are x/y (easting/longitude first); the authority axis order is recorded and used only for display. PROJ network access is disabled.
- **Alternatives:** Store everything only in EPSG:4326; store only in source CRS and transform on read.
- **Trade-offs:** Storing both costs space but makes round-trip checks and provenance direct.
- **Approval required:** No for the mechanism. Grids and DSM are U-006 and U-007.

### D-012 Aggregation split: deterministic facts in code, narrative only from the LLM

- **Context:** AGG-001, R-019–R-021, T-09, V3 §6.4.
- **Decision:** Grouping, duplicate-lineage checks, independent-source counts and conflict detection are deterministic code. The LLM receives a validated JSON fact bundle and returns schema-constrained narrative items, each citing observation/asset IDs. A validator rejects output that cites an unknown ID or contains a number, coordinate or timestamp not present in the cited facts. On failure the system retries a bounded number of times, then falls back to a deterministic template. All numbers and tables in reports are rendered from the database, never from LLM text.
- **Alternatives:** Let the LLM read raw pipeline output and write the whole report; no LLM at all.
- **Trade-offs:** Less fluent text; far smaller hallucination surface; reports still work when the LLM is unavailable.
- **Approval required:** No.

### D-013 Duplicate policy: accept, link and flag

- **Context:** ING-001 requires a deterministic duplicate policy; SRC-002 and T-06 require duplicates not to count as independent corroboration. The spec does not say whether a duplicate upload should be rejected.
- **Decision:** A byte-identical upload creates a new `asset` row pointing at the existing blob, with `duplicate_of_asset_id` set and a `DUPLICATE_ASSET` flag. Assets sharing a blob or a lineage root form one independence group. Perceptual similarity only ever produces a "possible duplicate" candidate for review.
- **Alternatives:** Reject the second upload; silently reuse the first asset.
- **Approval required:** **Yes — before Phase 2.**

### D-014 Report rendering: HTML templates; PDF by headless Chromium

- **Context:** V2 Screen 11 requires PDF/HTML export. Environment: Windows, no GTK.
- **Decision:** Jinja2 templates render HTML from stored records. PDF is produced by printing that HTML with the Playwright-managed Chromium that is already needed for end-to-end tests.
- **Alternatives:** WeasyPrint (GTK dependency on Windows); ReportLab/fpdf2 (second layout to maintain).
- **Trade-offs:** One layout for both formats; a browser binary becomes a runtime dependency (about several hundred MB, works offline once installed).
- **Approval required:** **Yes — before Phase 5.**

### D-015 Raster display through derived Cloud-Optimized GeoTIFFs and an in-process tile endpoint

- **Context:** Large GeoTIFFs cannot be sent to the browser; originals must not be modified.
- **Decision:** Ingestion creates a COG derivative (lineage recorded, checked per RAS-001). A tile endpoint serves map tiles for georeferenced rasters. Rasters without a valid CRS are served only to the image-space viewer, never to the map.
- **Alternatives:** Pre-rendered tile pyramids; a separate tile server; client-side GeoTIFF decoding.
- **Approval required:** No.

### D-016 No external network calls by default

- **Context:** V2 §2 and §10, brief §13.
- **Decision:** Default configuration has no basemap URL, no geocoder, no Cesium ion token, no telemetry, PROJ network off, Hugging Face offline mode on, and an LLM host restricted to loopback. Every online integration is an explicit Settings entry that is off until configured, and each use is recorded.
- **Approval required:** No.

### D-017 Pipeline adapter interface with a deterministic stub model

- **Context:** Models are not selected (U-001). The plumbing still has to be built and tested.
- **Decision:** Each pipeline defines an adapter interface (`load`, `infer`, `capabilities`, `score_semantics`). A stub adapter that returns fixed outputs from fixtures is used for contract, job and UI tests. Real adapters are added once models are approved.
- **Trade-offs:** Phases 1, 2, 4 and parts of 3 and 5 can proceed without a model decision. Passing tests with the stub says nothing about real model quality, and the documents must say so.
- **Approval required:** No.

### D-018 Authentication for the PoC: local single-workspace mode

- **Context:** V2 Screen 1 says "credentials if enabled"; V2 §14 lists the authentication model as undecided.
- **Decision (proposed default):** The API binds to 127.0.0.1 only. A local profile (display name) is chosen at start and recorded as the reviewer on every audit row. Optional local accounts with password hashing can be enabled by configuration; no external identity provider.
- **Alternatives:** Mandatory local accounts with roles; OS-account identity.
- **Trade-offs:** Fast to build and honest for a single-user PoC. It provides attribution, not access control: anyone with access to the Windows session can act as any profile.
- **Approval required:** **Yes — before Phase 1.**

### D-019 Test tooling

- **Decision:** pytest (+ httpx test client) for unit and integration; Schemathesis against the OpenAPI document for contract tests; Vitest + React Testing Library for frontend units; Playwright for browser end-to-end, including a network-blocked offline run; synthetic rasters generated in test code with Rasterio for geospatial correctness; pytest-socket to prove no outbound connections in offline tests.
- **Alternatives:** Jest; Cypress; Dredd.
- **Approval required:** No.

### D-020 Scope guard for OPS-001

- **Context:** OPS-001 and T-12 require refusal of operational targeting requests.
- **Decision:** Three layers. (1) Structural: the schema, taxonomy and report templates have no fields for priority-for-engagement, weapon, impact point or engagement plan. (2) Input: any free-text instruction passed to the aggregator goes through a guard step that returns a fixed refusal plus an offer of an evidence-only report. (3) Output: the aggregator output schema cannot express such decisions and the validator rejects text matching a maintained deny-list. T-12 prompts are a permanent regression fixture.
- **Alternatives:** Prompt-only refusal.
- **Trade-offs:** A deny-list has false positives and false negatives; the structural layer is the primary control.
- **Approval required:** No.

### D-021 Repository layout

- **Decision:** Keep the suggested top-level folders with two adjustments: pipeline code lives in an importable Python package under `ml/`, and model **weights** live under `DATA_ROOT`, not in `models/` (which holds only the manifest and model cards). Details in the blueprint §6.
- **Approval required:** No.

## Part B — Conflicts found inside the specification

These are not design choices; they are inconsistencies that need a ruling.

| ID | Finding | Proposed handling |
|----|---------|-------------------|
| S-01 | The file is named `..._v3.docx` and contains a "Version 3.0" section, but the cover page says "Revision 2.0" and the page footer says "Version 2.0". | Treat the whole file as the approved Version 3.0. Confirm. |
| S-02 | Two observation schemas with different field layouts (V2 §6, V3 §4). | D-009. |
| S-03 | Two review-status vocabularies (V2 §6, V3 §4–5). V3 §5's table says "accepted/proposed" while the V3 §4 schema enum says only `proposed`. | D-008; use `proposed`. |
| S-04 | Quality flags upper-case in V2 (`UNKNOWN_CRS`, `NOT_GEOREFERENCED`) and lower-case in V3 (`missing_crs`). | D-008. |
| S-05 | V2 §13 starts with the app shell; V3 §9 says to implement ingestion, IDs, checksums and provenance first. | Plan keeps the V2 six phases and puts the V3 §9 foundations at the start of Phases 1–2. |
| S-06 | V2 §11 lists `PATCH /api/v1/observations/{id}/review`; the Phase 0 brief's candidate list omits it and adds `GET /api/v1/projects/{id}/scene`, which the spec does not list. | Include both. |
| S-07 | The acceptance checklist says to "analyse one compatible GeoTIFF independently and flag unknown CRS", which reads as one test but needs two fixtures (valid CRS, missing CRS), as T-03 and T-04 make explicit. | Test both. |
| S-08 | Every V3 rule carries "Rule status: Proposed general-analysis rule", and V3 says slide references must be confirmed against the rendered deck. The PowerPoint is not in the repository. | Treat the rules as approved requirements for this PoC; mark all PPT slide references "not verified" in the traceability matrix. |

## Part C — Unresolved decisions

The first six restate spec V2 §14 and V3 §8; they were already open in the approved document.

| ID | Decision needed | Source | Blocks | Proposed default if no answer |
|----|-----------------|--------|--------|-------------------------------|
| U-001 | Model family for each of the three pipelines, the supported classes, and a labelled validation set for each | V2 §14, V3 §8 | Phase 3 | None. Stub adapter only (D-017); no real model is selected without sample data. |
| U-002 | Which Qwen model/quantisation to use for aggregation on an 8 GB GPU | Brief §5 | Phase 5 | None selected. Candidates must fit about 6 GB VRAM; measure before choosing. |
| U-003 | Supported file formats and maximum sizes per modality | V2 §14, V3 §8 | Phase 2 | Configurable limits with conservative defaults proposed in the blueprint §12; formats limited to what the installed decoders demonstrably read. |
| U-004 | Confidence calibration, thresholds and review policy per model/class | V2 §14, V3 §8, R-011, CON-002 | Phase 3 | No thresholds. Every score is labelled uncalibrated and every observation needs review. |
| U-005 | Minimum image dimensions and quality criteria per model | V3 §8 | Phase 3 | Decode and dimension checks only; blur metric reported as a raw value with no pass/fail claim. |
| U-006 | Authoritative CRS list and transformation grids to bundle offline; permitted coordinate tolerance per CRS | V2 §14, V3 §8 | Phases 2, 4 | PROJ's built-in EPSG database, no extra grids; transformations that need a missing grid are reported as reduced-accuracy or unavailable. |
| U-007 | An authoritative technical definition of the Defence Series Map (DSM) CRS | V2 §10 | Any DSM claim | DSM support is not claimed. The Settings screen shows the spec's statement. |
| U-008 | Report template, sections and export formats for the first demo | V2 §14 | Phase 5 | HTML + PDF with the R-023/REP-001 mandatory sections. |
| U-009 | Authentication, user roles, who may mark `verified`, retention policy | V2 §14, V3 §8 | Phase 1 (auth), Phase 4 (roles) | D-018; any local profile may review. |
| U-010 | Video sampling interval and tracking evaluation criteria; whether cross-frame association is in the PoC at all | V3 §8 | Phase 3 | Configurable fixed interval; association off by default. |
| U-011 | Minimum evidence requirements per observation class | V3 §8 | Phase 3 | At least one evidence reference with a region or frame (EVD-001). |
| U-012 | Which 3D data formats count as "supported" | V2 Screen 9 | Phase 6 | None until decided; the empty state is the only guaranteed behaviour. |
| U-013 | Offline basemap source and its licence | V2 §2, §10 | Phase 4 | No basemap: local layers on a plain background with a graticule. |
| U-014 | Whether online imagery discovery is in the PoC, and which sources are "configured and permitted" | V2 §2 | Phase 5 or later | Out of the PoC build; only the disabled Settings entry and provenance fields (R-007) exist. |
| U-015 | Controlled category taxonomy | V2 §6 | Phase 3 | Derived from the chosen models' class lists; anything else is `unclassified`. |
| U-016 | Whether to supply the source PowerPoint so slide references can be audited | V3 §1, §10 | Traceability sign-off only | References stay "not verified". The presentation's operational procedures stay out of scope either way. |

## Part D — Phase 1 outcomes (2026-10-12)

Approved by the project owner before Phase 1: D-003 (PostGIS in Docker on 5433), D-018 (single local workspace with a named profile; loopback only; not production authentication), git initialisation and Phase 1 dependencies. S-01 (Version 2.0 / 3.0 labels) is **recorded as unresolved**; the DOCX is the approved working specification and is preserved unmodified.

| ID | Decision or deviation | Reason |
|----|-----------------------|--------|
| P1-01 | Project root is the workspace folder `C:\Users\HP\Desktop\target`, not `D:\geopoc`. Caches are in `D:\geopoc-cache`, data root is `D:\geopoc-data`. Supersedes the location part of D-004 and the paths in `phase-1-readiness.md` §5. | Owner instruction during Phase 1 |
| P1-02 | `psycopg[binary]==3.2.10` and `pyproj==3.7.1` are pinned. | Windows Smart App Control (enforcing) blocks the native libraries in psycopg-binary 3.3.6 and pyproj 3.7.2. The policy was left untouched. New risk E-11: every package with native code must be load-tested before adoption. |
| P1-03 | Synchronous SQLAlchemy sessions instead of async. Amends blueprint §4.1. | Simpler; avoids the driver's Windows event-loop restriction. |
| P1-04 | UUID version 4 identifiers. Amends blueprint §7. | No built-in version 7 in Python 3.11. |
| P1-05 | Added `GET/POST /api/v1/workspace/profiles`; mutations require `X-Workspace-Profile`. | Implements D-018. |
| P1-06 | PostgreSQL data in a Docker named volume (inside Docker's disk image on C:), not a bind mount on D:. | Bind-mounted PostgreSQL data directories are unreliable on Windows; Docker data was not to be relocated. |
| P1-07 | TypeScript pinned to 5.x. | The OpenAPI type generator does not support TypeScript 7. |
| P1-08 | Browser tests use the installed Microsoft Edge. | Avoids a browser download. |
| P1-09 | `GET/PATCH /api/v1/settings` and a backend static type-checker deferred. | Not needed for the Phase 1 exit criterion. |

## Part E — Phase 2 outcomes (2026-10-12)

Approved by the project owner before Phase 2: D-009 (Version 3.0 observation schema is canonical), D-013 (duplicates: accept, link, flag), U-003 (formats and limits: JPEG/PNG/WebP 50 MB; GeoTIFF 1 GB; MP4/MOV/MKV 1 GB), and installing Rasterio and OpenCV subject to Smart App Control.

| ID | Decision or deviation | Reason |
|----|-----------------------|--------|
| P2-01 | Upload is a raw `application/octet-stream` body with `modality` and `filename` as query parameters, not multipart. Amends blueprint §8 endpoint 7. | Multipart parsing spools the whole file to the system temp folder on C:, which has under 7 GB free. A raw body streams directly to the D: data folder with constant memory. |
| P2-02 | Validation runs synchronously inside the upload request. The job queue and worker from the original Phase 2 plan are deferred. | Phase 2 was scoped to ingestion and metadata; every check is bounded and fast. Phase 3 pipelines will need the worker. |
| P2-03 | Previews, map tiles and COG derivatives (blueprint D-015) deferred. | Not in the approved Phase 2 scope. |
| P2-04 | Files that fail validation are not stored and do not become assets; the rejection is an audit record. | "Reject unsupported or malformed inputs"; avoids keeping unreadable bytes. |
| P2-05 | `rasterio==1.4.3`, `pillow==10.4.0` pinned. OpenCV not installed. | Smart App Control blocks Rasterio 1.4.4. OpenCV was not needed: Pillow and the installed FFmpeg cover the phase. |
| P2-06 | Raster georeferencing is reported as two facts: `crs_status` (`valid`/`missing`/`invalid`) and `georeferenced` (true only with a valid CRS, a usable geotransform and coordinates inside the CRS domain). Refines blueprint §13 item 5. | A valid CRS with no geotransform, or with impossible coordinates, is not usable georeferencing. |
| P2-07 | EXIF GPS values are not stored; only their presence is recorded. EXIF capture time becomes a timestamp only when the file states its UTC offset. | Ground photographs must not acquire a location or an ambiguous time from unverified metadata. |
| P2-08 | Duplicates link to the first asset with that content in the same project. The same content in another project reuses storage but is not flagged. | Deterministic policy required by ING-001. |
| P2-09 | Backend line length raised from 110 to 120. | Readability of the new modules. |

**Newly unresolved — U-017:** the Phase 2 approval lists the review vocabulary as `proposed`, `verified`, `inconclusive`, `disputed`, while the adopted V3 schema also has `needs_review` and `rejected`, which R-012, R-013, REV-001 and the review screens rely on. Not implemented either way. See `observation-schema-notes.md` §2. This supersedes the open part of D-008.

## Part F — Phase 3 outcomes (2026-10-12)

Decided by the project owner before Phase 3: **U-017 resolved** — all six Version 3.0 review states are used. V3 is the canonical schema basis with the pixel convention and the "at least one evidence reference" rule preserved. The job worker and previews are built at the start of Phase 3. GPU feasibility and one ground-image model follow. Overhead and video inference are out of scope for this phase.

| ID | Decision or deviation | Reason |
|----|-----------------------|--------|
| P3-01 | Eighteen review-state transitions are permitted (table in `observation-schema.md` §5). Only a reviewer may verify; rejection always records a reason; `inconclusive` cannot go straight to `verified`; `disputed` is raised only by conflict detection and left only with a documented resolution; `verified` and `rejected` are reopened, never flipped. | Keeps pending review, uncertainty, disagreement and rejection distinct, as instructed. |
| P3-02 | V2 `summary` is merged into V3 `claim.description`; V2 `unresolved` maps to `needs_review`. All 21 V2 fields are retained or mapped; none is dropped. | Same purpose; `inconclusive` and `disputed` are narrower than `unresolved`. Both are judgement calls and easy to change. |
| P3-03 | Added to the V3 contract: `schema_version`, `score.value_range`, the convention fields on `region_px`, `review.reviewed_by`. | Needed to enforce the contract. V3 §5 requires recording who verified. |
| P3-04 | `ground_image` and `video` observations can never carry a geographic geometry; `derivation_method` is limited to `raster_affine_transform` and `human_entered`. | No georeferencing method exists for them; pixels must never become coordinates. |
| P3-05 | The pixel convention is "as stored": EXIF orientation is not applied to regions or previews. | One unambiguous grid, so overlays line up. |
| P3-06 | Each job runs in its own child process of the worker (implements D-002). | Dependable cancellation; all memory, including VRAM, is released when a job ends. |
| P3-07 | Observations are stored as the validated canonical document in one immutable JSON column plus indexed summary columns. Evidence references are inside the document, not a separate table. Amends blueprint §7. | Smallest design that satisfies Phase 3. Review history and geometry columns come with Phase 4. |
| P3-08 | Pipeline code lives in `services/api/geopoc/pipelines/`, not a separate top-level `ml/` package. Amends D-021. | One Python project and environment; pipelines stay isolated from each other. |
| P3-09 | PyTorch `2.11.0+cu128` and torchvision `0.26.0+cu128`, as an optional `ml` dependency group. | Verified on the RTX 5060; allowed by Smart App Control; the application runs without it. |
| P3-10 | The Python environment lives at `D:\geopoc-cache\venv-api` (`UV_PROJECT_ENVIRONMENT` in `scripts\env.ps1`). Source code stays in the project folder. | The environment is 4.4 GB; C: has under 7 GB free. |
| P3-11 | First model: `fasterrcnn_resnet50_fpn_v2` (COCO) from torchvision. **Resolves U-001 for the ground-image pipeline only**, as a first integration, not as an evaluated choice. | General-purpose, permissive code licence, no extra dependency, small VRAM footprint. |
| P3-12 | `min_score: 0.5` in the manifest is an operating cutoff: detections below it are counted on the run and not stored. It is not a calibrated threshold. | Without a cutoff the model emits up to 300 boxes per image. R-011 allows per-model configuration; nothing is claimed about correctness. |
| P3-13 | Every model observation starts as `needs_review` with `UNCALIBRATED_SCORE`. | CON-002: no validated threshold exists (U-004 still open). |
| P3-14 | A test-only stub detector exists for automated tests and is refused by the manifest loader unless `GEOPOC_ALLOW_STUB_MODELS` is set. | Tests the pipeline without a GPU; can never appear in a working installation. |
| P3-15 | The review API and revision history are not built; the transition rules exist as tested logic only. | They belong to Phase 4 in the implementation plan. |
| P3-16 | Model weights are fetched once by an explicit command that checks size, disk space and checksum. The application never downloads anything. | Local-first; nothing is fetched at run time. |

**Still open:** U-002 (Qwen model), U-004 and U-005 (calibration, thresholds, quality criteria — blocked on labelled data), U-006, U-007, U-008, U-009, U-010, U-012, U-013, U-014, U-015 (taxonomy beyond the model's own classes), U-016, and U-001 for the overhead and video pipelines. New: **U-018** — whether the model's training-data licence terms suit the project's intended use beyond a PoC.

## Part G — Decisions before the Phase 3.5 UI review (2026-10-12)

Decided by the project owner:

| ID | Decision | Effect |
|----|----------|--------|
| G-01 | **Accepted:** V2 `summary` maps to V3 `claim.description`, preserving the original information. | Confirms P3-02. The converter carries the V2 text across unchanged; there is one field instead of two. |
| G-02 | **Accepted:** V2 `unresolved` maps to V3 `needs_review`. | Confirms P3-02. Semantic mapping: V2 `unresolved` meant "not yet settled by review". V3 `inconclusive` (the input cannot support the claim) and `disputed` (supported observations conflict) are narrower, so pending review is the faithful target. |
| G-03 | **Continue using `fasterrcnn_resnet50_fpn_v2` (COCO) for local PoC evaluation.** The exact licensing review of the model weights is still pending. | U-018 stays open and must be closed before any use beyond local PoC evaluation. The model card says so. |
| G-04 | **No accuracy and no validated confidence threshold is to be claimed without labelled evaluation data.** | Standing rule. Scores stay labelled uncalibrated, every model finding starts as `needs_review`, and the 0.5 cutoff stays described as an operating setting. U-004 and U-005 stay open. |

Phase 3.5 is a UI/UX review and stabilisation of Phase 1–3 functionality only. No Phase 4 work is included.

## Part H — Decisions made in Phase 4 (2026-10-21)

Made during implementation, within the approved plan. Each can be changed; none claims a validated value.

| ID | Decision | Reason |
|----|----------|--------|
| P4-01 | The next phase is Phase 4 of the implementation plan (map, observations, review, audit, conflicts, exports). Overhead-raster and video analysis are not started: no model is selected for them (U-001). | The approved plan orders Phase 4 after Phase 3; its dependencies are met. Raster and video inference are blocked by a decision, not by code. |
| P4-02 | Review history is an append-only `observation_revision` table. The current state lives in `observation.review_status`, `current_category` and `revision`; the model's output (`record`, `category`, `score_raw`) is protected by a database trigger. Amends blueprint §7, which put the original output in a separate `raw_output` column. | One immutable document already exists (P3-07); a trigger makes "never overwritten" true for every client, not only the API. |
| P4-03 | Optimistic concurrency: every review action carries `expected_revision`; a mismatch is refused with `409 REVISION_CONFLICT`. | PT-18: no lost updates when two reviewers act at once. |
| P4-04 | Review actions are `confirm`, `reject`, `mark_inconclusive`, `reopen`, `relabel` and `note`. Their state changes follow the existing state machine for a reviewer; `relabel` and `note` keep the state and always need a reason. The UI offers exactly the actions the API reports as allowed. | REV-001, T-11. One rule set, enforced on the server. |
| P4-05 | Any local profile may review (U-009 default). Reviews are attributed to the profile; this is not authentication. | U-009 remains open; roles and who may verify are not decided. |
| P4-06 | Relabelling accepts free text (1–100 characters); the model's class is kept beside it. | U-015 (taxonomy) is open. A controlled list can replace free text later without losing history. |
| P4-07 | Conflict and possible-duplicate rule `pixel_overlap_iou_0.5_v1`: findings from different runs on the same image content (same asset or byte-identical copy) whose boxes overlap with IoU ≥ 0.5 are linked as possible duplicates when the categories agree, and recorded as a category conflict when they differ. Conflicting findings are set to `disputed` by the system where the state rules allow. Rejected findings take no part. The 0.5 is an operating setting, not a validated value. | CON-001, T-07, R-016. Deterministic and explainable; nothing is merged or averaged. |
| P4-08 | A conflict is closed (`resolved_documented`) when none of its members is still `disputed`; the reviewer's reason becomes the resolution note. Members are never removed (trigger). | CON-001: preserve both sides; document the resolution. |
| P4-09 | Map tiles are rendered on request from the stored original: the source window is read (decimated when coarser), reprojected with GDAL from the raster's own CRS and full affine to Web Mercator, and cached in memory (512 tiles). The display stretch is the preview's, so tiles and preview match. No COG derivative is made. | Correct for rotated rasters; no extra stored files. A COG/overview cache is follow-up work for very large rasters (see the Phase 4 report). |
| P4-10 | No basemap (U-013 default): a plain background with a 10° graticule. The map style references no online source, glyphs or sprites. | Local-first; U-013 is open. |
| P4-11 | MapLibre GL JS 5.24.0 (BSD-3-Clause), bundled and loaded only on the map screen. | Blueprint §13.10 names MapLibre; bundling keeps it offline. |
| P4-12 | Coordinate readout: decimal degrees with hemisphere and DMS are formatted in the browser from the stored number; conversion to another registered CRS is done by the API with PyProj (`always_xy`, network off) and reports accuracy and an area-of-use warning. | GEO-002, PT-17. |
| P4-13 | Exports are synchronous API responses (CSV, GeoJSON, JSON), audited, not stored as files. CSV image regions are in columns labelled as source-image pixels; GeoJSON contains only observations with a geographic location and states how many were left out. Amends blueprint endpoint 29 (job-based). | The data is small; a job adds nothing yet. CL-13, PT-16. |
| P4-14 | The worker probes PyTorch/CUDA in a background thread and takes jobs immediately; until the probe finishes, real models report "still checking". | The probe took 40–70 s on a cold start and delayed previews and tests. |

**Still open:** as Part F, plus U-009 (roles) and U-013 (basemap) now each have a working default in use.

## Part I — Phase 4 follow-up: georeferencing recorded by a person (2026-10-21)

| ID | Decision | Reason |
|----|----------|--------|
| P4-15 | Two separate operations: **assign_crs** (state the CRS of the raster's *existing* coordinates; offered only when a usable geotransform exists) and **set_geotransform** (enter six affine parameters or a world file from a documented source, with a CRS when none is usable). A third, **revert_to_file**, restores the file's own metadata. Implements blueprint endpoint 13 and §13.6, which named only the CRS case. | A CRS alone never gives pixels a position. Keeping the cases apart prevents a CRS choice from looking like georeferencing. |
| P4-16 | Only rasters whose own georeferencing is missing or invalid can have georeferencing recorded. A raster whose file declares a valid CRS and geotransform is never overridden (409 `ALREADY_GEOREFERENCED`). | No silent replacement of valid source metadata. |
| P4-17 | Nothing is inferred: no CRS or coordinates from the filename, project location, appearance or other data. Ground control points are not used (not supported). | GEO-001, R-005. |
| P4-18 | `raster_metadata` stays the file's metadata as ingested; a trigger now refuses any change to it except two pointer columns (`georef_version`, `current_assertion_id`). Each change is an append-only `georef_assertion` row with previous and resulting metadata, validation checks, corner mapping, transformation, the original's SHA-256 (re-hashed at recording time), reason, profile and request ID. | Same pattern as observation review (P4-02). The original file is never written. |
| P4-19 | Validation runs on the server for both the preview step and the recording step: CRS resolves in the local registry and is 2D; geotransform has six finite numbers, non-zero area, is not the identity; the outline transforms to valid longitude/latitude; transformation accuracy; area of use (warning); units plausibility (warning); a map tile is actually rendered from the original with the proposed metadata. Any failed check stores nothing. Warnings are shown and kept with the version. | Real checks with PROJ and GDAL, not form validation. "Passed" means usable, not correct. |
| P4-20 | Recording requires a reason, an explicit confirmation flag, and `expected_version`; a mismatch is refused (409 `GEOREFERENCE_VERSION_CONFLICT`). | No silent overwrite of another person's change. |
| P4-21 | Placement from recorded metadata is labelled `user_asserted` (UI: "placed from recorded georeferencing"), and a `CRS_USER_ASSERTED` flag exists for any future derived result. | Blueprint §13.6. |
| P4-22 | Tiles for a recorded version are rendered from the unchanged original with the recorded CRS and geotransform passed explicitly. The tile cache key and the tile URL (`?v=`) include the version, and the map uses a new source per version, so no tile made with older metadata is reused. No derived file is written. | Cache correctness without storing derivatives. |
| P4-23 | World files are read as A, D, B, E, C, F with the pixel-centre origin converted to GDAL's corner origin by subtracting half a pixel along both axes. | The documented world-file convention. |
| P4-24 | Any local profile may record georeferencing (U-009 default). This is attribution, not authorisation. | U-009 is open. |

## Part J — Phase 5 readiness: options and recommendations (2026-10-21)

Planning only; nothing implemented, downloaded or installed. Full reasoning, comparison tables, evaluation protocol and sources: [../phase-5-readiness.md](../phase-5-readiness.md). **Every row needs owner approval** (marked "Owner"); none is decided by this entry.

| ID | Question | Options and trade-offs | Recommendation | Status |
|----|----------|------------------------|----------------|--------|
| P5-01 | Where raster/video analysis sits | Plan Phase 3 scope deferred; plan Phase 5 is aggregator/reports. Insert "Phase 3b" now (aggregation then has more than one pipeline) vs keep plan order | Insert Phase 3b before plan Phase 5 | Owner (O-1) |
| P5-02 | Imagery to support (U-001 prerequisite) | Model choice depends on sensor, GSD, bands and bit depth; VHR RGB and Sentinel-2/HLS need different models | Owner supplies 3–5 representative files | Owner (O-2) |
| P5-03 | Use cases | UC-R1 land cover, UC-R2 buildings, UC-R3 general objects (needs explicit category list under OPS-001), UC-R4 change, UC-R5 assisted delineation | UC-R5 and non-AI tools first; then UC-R1 or UC-R2 | Owner (O-3) |
| P5-04 | Licence posture | (a) PoC evaluation only: AGPL-3.0 code and academic-only training data (DOTA, LoveDA, LEVIR-CD, xView) acceptable as under G-03; (b) commercial/operational: permissive code + weights + training data only (SAM 2.1, Prithvi-EO-2.0, Clay, TorchGeo CC0/CC-BY weights, EuroSAT, BigEarthNet, SpaceNet with ShareAlike review) | State (a) or (b); legal review of non-commercial evaluation use | Owner (O-4) |
| P5-05 | Models (U-001, overhead) | See readiness §3. Avoid MMRotate/Detectron2 (compiled CUDA extensions on Windows + Blackwell + Smart App Control) | SAM 2.1 (UC-R5); Prithvi-EO-2.0 or TorchGeo backbone + permissive head (UC-R1 multispectral); YOLO11-OBB DOTA weights for PoC evaluation only (UC-R3 VHR) | Owner (O-5) |
| P5-06 | Evaluation data | Public benchmarks are indicative only and several are non-commercial; acceptance needs representative owner-labelled data | Owner-labelled set required for acceptance (proposed ≥ 200 tiles per use case, ≥ 50 instances per class) | Owner (O-6) |
| P5-07 | Acceptance criteria | Hard gates (provenance 100 %, no geometry without valid georeferencing, no network, VRAM < 7 GB, reproducible, originals unchanged) and per-class quality gates | Hard gates as proposed; quality numbers set by the owner after an indicative run; until then outputs stay `needs_review` and uncalibrated | Owner (O-7) |
| P5-08 | Downloads and dependencies | Weights, `sam2`, `ultralytics` (AGPL-3.0), `terratorch`, datasets; sizes listed before download | Approve item by item | Owner (O-8) |
| P5-09 | Video sampling (U-010) | A fixed time interval by PTS; B every Nth frame; C keyframes; D scene change; E every frame (coverage vs cost vs reproducibility; see readiness §6) | **A**, 1 frame/s default, configurable 0.2–5 fps, first and last frame always; record frame index, PTS, timestamp_ms, decoded-pixel SHA-256 and settings; store evidence frames losslessly; disclose coverage | Owner (O-9) |
| P5-10 | Video frame model and association | Reuse the ground detector (licence decided under G-03) vs another model; association on/off | Reuse ground detector; association off; no location from video | Owner (O-10) |
| P5-11 | Non-AI raster capabilities | N-1 to N-10 (readiness §8), labelled `metadata_derived` or measurement, never as AI | Build N-1, N-2, N-3, N-5, N-6 first; needs no model or data | Owner (O-11) |
| P5-12 | U-018 per chosen model | Training-data licence beyond PoC | Close per model card before any use beyond PoC evaluation | Owner (O-12) |

**Not started, by rule:** no inference implementation until P5-02 to P5-08 are approved; no video inference until P5-09 and P5-10 are approved.

## Part K — Owner decisions: Phase 3b approval (2026-10-21)

Decided by the project owner. These answer Part J (P5-01 to P5-12) where stated; the rest of Part J stays open.

| ID | Decision | Effect |
|----|----------|--------|
| K-01 | **Approved:** insert Phase 3b before the original Phase 5. Phase 5 keeps its purpose (summary generation, versioned reports, offline verification). | Implementation plan Amendment A1. Answers P5-01 / O-1. |
| K-02 | **Approved for implementation, in order:** band selection and contrast; pixel values and band statistics; distance and area measurement (valid georeferencing, units and method only); efficient display copies or overviews preserving original bytes; non-AI pixel comparison of compatible, aligned rasters. Then **plan** `.tfw`, `.prj` and `.aux.xml` sidecar support with review-and-confirm. | Answers P5-11 / O-11 (extended). No silent inference of band identities, no modified originals, no invented spatial metadata, no unsupported units. |
| K-03 | **No model, weight or dataset downloads are authorised.** SAM 2.1 person-guided outlining is the first AI candidate to *evaluate*. Its exact download sizes, dependencies, Windows/RTX 5060 compatibility, code and weight licences, dataset terms and restrictions are to be presented before approval is requested. Autonomous building detection, land cover and general object detection wait until imagery, categories and evaluation data are agreed. All future AI results are proposed findings requiring review. | P5-05 and P5-08 remain open; SAM 2.1 is the first candidate. |
| K-04 | **Licensing:** authorisation is limited to local PoC evaluation. No commercial-use permission is claimed. Code, weights, training data and evaluation datasets are checked independently before each approval. No public dataset is downloaded until the exact dataset, use, licence and size are approved. | Answers P5-04 / O-4 as option (a) for now. |
| K-05 | **Evaluation:** prepare a representative imagery inventory and a reproducible benchmark plan. No accuracy claims and no numerical thresholds before indicative runs and labelled-data review. Geospatial correctness, provenance, repeatability, network isolation and original-file integrity are mandatory gates. | P5-06 and P5-07: hard gates confirmed, quality thresholds deferred. |
| K-06 | **Video sampling policy recorded for future implementation:** 1 frame/s default, configurable 0.2–5 frames/s; first and last frames included; frame index, source timestamp, decoded-frame checksum and extraction settings preserved; coverage reported, with the warning that short events can be missed; cross-frame tracking out of scope initially. **No video model downloads or inference are authorised.** | Answers P5-09 / O-9 for the policy. P5-10 stays open. |
| K-07 | **Execution:** incremental, with automated tests and a real application workflow for each increment; synthetic data for controlled tests, with limitations stated; the current design system; local-first; no commits; stop at the Phase 3b gate. | Applied in the Phase 3b report. |

### Engineering decisions made while implementing K-02

| ID | Decision | Reason |
|----|----------|--------|
| P3b-01 | Display settings (bands; stretch: default, none for 8-bit only, min–max, 0.5–99.5 / 1–99 / 2–98 / 5–95 percentiles, manual) are request parameters of tiles and image rendering, never stored as data. Bands are offered by number with the file's own description and colour interpretation. | Display only; no band identity is inferred (K-02). |
| P3b-02 | Band statistics are a background job writing a derived JSON artifact. They are exact up to 16 M pixels per band; above that, an evenly spaced nearest-neighbour sample of real values is used, and the method and fraction are recorded. Queued automatically for every raster on upload. | Bounded memory and time; exact and estimated are told apart. |
| P3b-03 | Pixel values are read from the original, with nodata, scale/offset and units only as declared by the file. A map position is converted through the effective georeferencing (file or recorded). | The original is the source of truth. |
| P3b-04 | Measurements are geodesic (PROJ/Karney) on the CRS's own ellipsoid, after mapping pixels through the affine transform; projected CRSs also get a planar value labelled as including projection distortion. Without valid georeferencing: `unavailable`, with pixel units only. Recorded georeferencing adds `CRS_USER_ASSERTED`. Map measurements are geodesic on WGS 84; lines across the antimeridian and self-intersecting outlines are refused. | K-02: valid metadata, units and method only. |
| P3b-05 | Display copy: a COG (DEFLATE, lossless, same type, nodata, CRS and geotransform) with average overviews. It is built automatically for rasters larger than 4,096 px on a side that have no overviews of their own, or on request. It is kept only after 25 full-resolution windows and all metadata match the original. Used only for drawing; statistics, pixel values, measurements and comparisons always read the original. | Large-raster drawing (a Phase 4 limitation) without risk to originals. |
| P3b-06 | Pixel comparison: B − A of one band each. Only for rasters on the same grid (both georeferenced, same CRS, geotransform within 1e-9 relative, same size); nothing is resampled. Nodata, masked or non-finite pixels in either input are NaN. The result is stored as a new **derived asset** (`origin = derived`, `DERIVED_ASSET`, and `CRS_USER_ASSERTED` when an input used recorded georeferencing) with append-only `asset_lineage` rows holding the parents' checksums and georeferencing versions. | Traceable; a pixel difference is not presented as change interpretation. |
| P3b-07 | Existing assets get statistics and display copies only on request; nothing existing is altered automatically. Phase 4 guarantees are unchanged. | No silent changes to existing data. |

### Sidecar metadata follow-up (2026-10-21)

The owner accepted the five non-AI raster tools for the local PoC, and approved sidecar discovery with review-and-confirm. Implementation details are in sidecar-metadata-plan.md.

| ID | Decision | Reason |
|----|----------|--------|
| P3b-08 | Sidecars are stored unmodified and content-addressed in the blob store, with an append-only `sidecar` row. They are re-parsed from their verified bytes whenever reviewed or used; parsed values are not stored. `GDAL_PAM_ENABLED=NO` is set for every process. | Originals and sidecars are never changed, and what a person confirms is exactly what the file says. GDAL never reads or writes PAM files on its own. |
| P3b-09 | A `.prj` or `.aux.xml` CRS is used only when exactly one registry CRS matches it at 100 % confidence, resolves through the existing machinery, is equivalent (axis order aside), and is 2D geographic or projected. Otherwise it is shown as unresolved, with the nearest matches. | Owner requirement: accept only definitions the existing CRS machinery can resolve and validate. Names are not trusted. |
| P3b-10 | Sidecar values become a proposal recorded only through `georeference.validate`/`apply`, with a reason, a confirmation, the reviewed version and the reviewed set of sidecars. The methods are `sidecar_geotransform` and `sidecar_crs`; `validation.sidecars` lists the files and checksums used. | Same validation, versioning, audit, checksum verification and tile versioning as georeferencing entered by hand. |
| P3b-11 | Valid raster-owned metadata is never replaced. Disagreements are shown and the file's values kept. A world file alone does not establish a CRS. Disagreeing sidecars need an explicit choice. | Owner requirements 4, 9 and 10. |
| P3b-12 | `.aux.xml` band metadata (description, nodata, scale/offset, units, colour interpretation) is parsed and compared but not applied. GCPs, RPCs and unknown elements are listed as present, not used. | Applying band metadata would change the accepted raster tools; it needs its own review record. Not built (remaining gap). |
| P3b-13 | No new dependency: `.aux.xml` is parsed with the standard-library ElementTree, refusing DOCTYPE/ENTITY declarations. | Expat 2.6.0 limits entity expansion and ElementTree fetches no external entities. defusedxml is not needed. |

## Part L — CRS correctness hardening, Phase 2 (2026-11-04)

Owner-approved policies and decisions, implemented without a migration (working database stays at revision 0009; migration 0010
stays unapplied; no 0011 exists).

| ID | Decision | Reason |
|----|----------|--------|
| L-01 | A datum shift whose documented accuracy is worse than **10 m** (`ACK_ACCURACY_M`), or has no stated accuracy, is not used until a person acknowledges that specific operation. Exactly 10 m needs none. Ballpark and missing-grid operations are never unlocked by an acknowledgement. | The registry has 870 transformations with no stated accuracy and 251 above 10 m; Kalianpur 1962 / UTM 43N (999 m) was applied silently. |
| L-02 | An acknowledgement matches only the same operation, source CRS, target datum and accuracy value; any change makes it stale. It is built from the refusal, not typed. | A person can only accept what was shown. |
| L-03 | D1: acknowledgements are stored only in a new georeferencing version's existing JSON (`validation.transformation.acknowledgement`) and in the area of interest's `aoi_source`. File-georeferenced rasters and legacy recorded versions that need one **fail closed**. `audit_event` is not used and no georeferencing operation is mislabelled. | Recording one for those would need a schema change; no workaround was invented. A request can never unlock stored data. |
| L-04 | D2: unknown accuracy (a registered shift that is not a ballpark) requires acknowledgement and is never selected automatically. | An unstated error could be any size. |
| L-05 | D3: control points are judged in metres from the ground resolution (`sigma = sqrt((p*g)^2 + s_ref^2 + s_src^2)`; warn above 2x, fail above 5x the expected `sqrt(2)*sigma`, allowing for degrees of freedom), with leave-one-out, shear by effect, corner uncertainty and evidence schema 2. Uncertainties the person does not state are reported as not stated and never assumed. | Pixel and map-unit thresholds mean different things for every scan; there is no universal threshold. |
| L-06 | D4: three points are allowed, with a permanent `EXACT_FIT_NO_CHECK` warning and no leave-one-out. | The fit is exact and cannot reveal a wrong point. |
| L-07 | D5: the stateless point calculator takes a per-request acknowledgement only and stores nothing. | It records nothing, so nothing can be asserted for stored data through it. |
| L-08 | D6: a difference between the EPSG databases of pyproj's PROJ and GDAL's PROJ is a warning with a fixed-coordinate parity self-test, not a start-up refusal. A parity failure, a major/minor PROJ difference or an enabled network is a failure, shown as one. `PROJ_NETWORK=OFF` is forced at package import. | The databases differ (v11.022 vs v11.016) in a small, measured way; positions are what matter. |
| L-09 | D7: antimeridian support is limited to footprints (split MultiPolygon, flagged). Other consumers refuse with `ANTIMERIDIAN_CROSSING_UNSUPPORTED`. Tile rendering for crossing rasters is deferred. | One continuous area or distance is needed for everything else. |
| L-10 | Orientation is measured on the ground from the CRS's local east/north basis, not from the sign of affine terms or axis names. | The old checks were wrong for EPSG:2046 (x west, y south) and polar grids. |
| L-11 | Ballpark is recognised structurally (`has_ballpark_transformation`); wording is corroboration; a disagreement fails closed and is logged. | A renamed operation must not hide a ballpark. |
| L-12 | An offline validation harness (`geopoc.validation.mapsheets`) checks sheets against independent reference points and reports NOT VALIDATED without real, authorised samples. | No real OSM or ESM sample exists here; nothing may be claimed validated without one. |

Still open: how many stored rasters and georeference versions the 10 m gate affects (a database read, not authorised); real
OSM and ESM samples; an authoritative ESM definition (no ESM datum, ellipsoid, projection or transformation is guessed); an
owner decision on whether to add a schema change that lets a file-georeferenced raster carry an acknowledgement.
