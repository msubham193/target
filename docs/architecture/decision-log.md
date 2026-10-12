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
