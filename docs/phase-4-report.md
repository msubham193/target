# Phase 4 report — map, observation review, conflicts and exports

Date: 2026-10-21. Status: **complete and awaiting review.** Phase 5 has not been started. Nothing has been committed.

## 1. Repository audit and phase selection

I traced each capability from the UI through the API and database to the stored result before writing any code. This section records the state at the start of the phase.

| Capability | State before Phase 4 | Evidence |
| --- | --- | --- |
| Login / local profile | Complete and verified. It is a local profile, not authentication; this is by design (D-018). | `workspace` router, e2e phase 1 |
| Projects, AOI, CRS search | Complete and verified | Phase 1 report, e2e phase 1 |
| Upload and ingestion | Complete and verified: checksums, duplicates, rejections, metadata, unknowns | Phase 2 report, e2e phase 2 |
| Jobs, worker, previews | Complete and verified | Phase 3 report, e2e phase 3 |
| Ground-image analysis | Complete for one model (fasterrcnn, COCO). It is unevaluated and its scores are uncalibrated. | Phase 3 report |
| Observation persistence | Partial: records were stored immutably, but there was no current state, history or detail endpoint | `observation` table, migration 0003 |
| Human review | UI only: a "Planned" badge. The state machine existed only as tested logic. | `contracts/review.py`; no endpoint |
| Conflicts / duplicate findings | Missing | — |
| Map & Analysis | UI only: a planned placeholder with a layer list. No map engine and no tiles. | `MapPage.tsx` |
| Raster display on a map / tiles | Missing. The plan's Phase 2 "georeferenced tile endpoint" was never built. | No route |
| Coordinate display conversion (DMS) | Missing | — |
| Exports | Partial: a browser-side JSON dump of the records only | `ReportsPage.tsx` |
| Satellite/raster analysis | **Blocked by a dependency:** no model is selected (U-001) | Decision log Part F |
| Video analysis | **Blocked by a dependency:** no model, sampling or association decision (U-001, U-010) | Decision log Part F |
| Reports (generation, versions, PDF) | Missing. Planned for Phase 5; depends on Phase 4 and on U-002, U-008 and D-014. | Implementation plan |
| 3D view | UI only (empty state). **Blocked:** U-012 (formats). Planned for Phase 6. | Implementation plan |
| Settings | Partial: real status, read-only. Editable settings are Phase 5. | `SettingsPage.tsx` |

**Phase selected: Phase 4 of the approved plan** (map, observations, review and audit). Its dependency, Phase 3, is met.

Your priority list puts raster analysis and video analysis before review. The approved plan puts the map, raster display and review together in Phase 4. Raster and video *analysis* cannot be built honestly yet, because no model or sampling decision exists for them. Choosing Phase 4 therefore covers your priority 1 (map and raster foundation) and priority 4 (persistence and human review). Priorities 2 and 3 stay blocked on decisions U-001 and U-010, not on code.

## 2. What was implemented

### Database (migration `0004`)

- **`observation`:** added `current_category` and `revision`. A trigger refuses any change to the model's output (`record`, `category`, `score_raw`, `asset_id`, `run_id`). Only the current state can change.
- **`observation_revision`:** append-only history. Each row records the action, the state and category before and after, the note, the actor (reviewer or system), the profile, the request ID and a timestamp. A trigger refuses UPDATE and DELETE.
- **Conflicts:** `conflict` and `conflict_member` hold category conflicts between findings. Members are append-only, and the resolution is documented on the conflict.
- **Possible duplicates:** `link_group` and `link_member` hold candidate links, which are append-only.
- **Rollback:** the migration downgrades cleanly; the migration test does upgrade, downgrade, upgrade.
- **Working database:** migrated by `scripts\dev.ps1` after a backup to `D:\geopoc-cache\backups\geopoc-before-0004-20261021-111748.dump` (verified with `pg_restore -l`). Its 12 existing observations kept their records unchanged and start at revision 0.

### Backend

- **Review service (`geopoc/review/service.py`):**
  - Actions: `confirm`, `reject`, `mark_inconclusive`, `reopen`, `relabel`, `note`.
  - State changes are checked against the existing state machine for a reviewer.
  - The observation row is locked during an action. `expected_revision` provides optimistic concurrency: a mismatch is refused with 409 `REVISION_CONFLICT`.
  - A reason is required wherever the rules require one; relabelling and notes always require one.
  - Each action is written to the audit log.
- **Linking (`geopoc/review/linking.py`):** runs after every ground analysis, using the deterministic rule `pixel_overlap_iou_0.5_v1`. It compares findings from *different runs on the same image content*, meaning the same asset or a byte-identical copy:
  - same category → a possible-duplicate candidate;
  - different category → a conflict, with both findings set to `disputed` by the system.

  Rejected findings take no part. A conflict closes when none of its members is still disputed, and the reviewer's reason becomes the resolution note.
- **Observation endpoints:**
  - `GET /projects/{id}/observations` returns each record with its current state, open conflicts, links and the actions allowed now. It filters by current status, current category, run, asset and conflict membership.
  - `GET /observations/{id}` adds the full history, conflicts and links.
  - `PATCH /observations/{id}/review` records one action.
  - `GET /projects/{id}/conflicts` lists conflicts.
- **Exports:** all three are audited and require a profile.
  - `GET /projects/{id}/exports/observations.csv`: image regions are in columns labelled "source image pixels, top-left origin"; the raw score is labelled "not a probability".
  - `.geojson`: contains only observations with a geographic location and states how many were left out and why.
  - `.json`: the original output, current state, revisions, conflicts and run provenance.
- **Map:**
  - `GET /projects/{id}/layers` returns:
    - placeable rasters, with footprint, bounds, native zoom, display settings and the recorded outline transformation;
    - excluded files, with a reason code (`CRS_MISSING`, `CRS_INVALID`, `NO_GEOTRANSFORM`, `INVALID_GEOREFERENCING` or `NOT_GEOREFERENCED`);
    - the AOI as a separate layer;
    - observation location counts;
    - the basemap status ("not configured").
  - `GET /assets/{id}/tiles/{z}/{x}/{y}.png` renders Web Mercator tiles on request from the **stored original, read-only**. It reads the source window from the inverse of the full affine transform (so rotation is honoured), decimates when the tile is coarser than the raster, reprojects with GDAL from the raster's own CRS, uses the preview's display stretch, and keeps a 512-tile in-memory cache. Rasters without valid georeferencing get 409 `CRS_UNRESOLVED`; tiles outside the raster get 204.
  - `GET /crs/transform` converts one point between registered CRSs with PyProj (`always_xy`, network off). It reports the accuracy and warns `OUTSIDE_AREA_OF_USE`, and it refuses "WGS84 + LCC" and non-2D CRSs.
- **Worker:** the PyTorch/CUDA capability probe now runs in a background thread, so previews start immediately. On a cold start the probe took 40–70 s and had been delaying every job. While it runs, real models report "still checking", and the UI re-checks until it finishes.

### Frontend

- **Map & Analysis View:** a real map built with MapLibre GL JS 5.24.0, loaded only on this screen.
  - There is no basemap and no online source: a plain background, a graticule, the raster tiles, raster outlines and the AOI.
  - The layer panel has a visibility toggle, an opacity slider, "Zoom to" and "Details" for each raster.
  - A "Not placed" list gives each excluded file and its reason, and "Fit to layers" re-frames the view.
  - The coordinate readout shows a live pointer position. A click pins a point, drawn on the map and shown in decimal degrees with hemisphere and in DMS. The pinned point can also be converted into any CRS used by the project, with accuracy and the axis-order note.
  - A layer-details panel shows the CRS, pixel size, extent, display stretch and outline transformation.
  - If WebGL is unavailable, an error notice replaces the map and the rest of the screen still works.
- **Review panel**, shared by Ground Image Analysis and Observations:
  - shows the current state and revision;
  - shows a correction note when the category was changed, with the model's class beside it;
  - shows open conflicts with links to the other findings, and possible-duplicate notes;
  - offers exactly the actions the API allows, with a required reason where needed;
  - explains a revision conflict and reloads the latest version;
  - shows the full history.
- **Observations:** filters by current state, conflicts and text; badges for "conflict" and "possible duplicate"; a corrected category is shown with the model's own label beside it.
- **Reports:** CSV, GeoJSON and JSON exports come from the API. PDF, HTML and GeoPackage are still labelled Planned.
- **Satellite Image Analysis:** an "On the map" panel. Band and contrast controls are still Planned.
- **Navigation:** Map & Analysis is no longer labelled "planned"; 3D View still is.

## 3. End-to-end workflow demonstrated

The walkthrough ran in Edge against the running application (API, worker, PostGIS and Vite) on a scratch stack: database `geopoc_review`, data folder `D:\geopoc-data\_review`. It used the **real detector on the GPU** and the review input files.

1. **Project and data:** created a project with an AOI entered in EPSG:32643. Uploaded `harbour_scene_utm43n.tif` (EPSG:32643, 10 m pixels), `scan_without_crs.tif`, `astronaut.png` and a byte-identical copy of it.
2. **Map:**
   - The georeferenced raster was drawn from 22 tiles, all HTTP 200. Tile latency in the browser was median 236 ms and max 323 ms (900 × 600 raster, cold cache).
   - The raster without a CRS and both photos were listed as not placed, each with its reason.
   - The AOI was drawn as a separate dashed layer.
   - A click pinned 27.095509° N, 75.042675° E, shown as 504230.420 E / 2997014.500 N metres in EPSG:32643. An independent PyProj conversion gives 504230.425 / 2997014.532, which agrees to the third decimal shown.
3. **Ground analysis (real model):** two findings, both `needs_review`.
   - Confirmed finding 1, which became revision 1, with history and attribution.
   - Rejected finding 2 with a reason.
4. **The copy, analysed with the same model:** its findings were linked as **possible duplicates** of the original's, stayed `needs_review`, and created no conflict. Relabelled one of them, "person" → "astronaut", with a reason; the model's class is kept beside it.
5. **Exports:** CSV (2,238 bytes), GeoJSON (507 bytes; no features, 4 excluded) and JSON (17,668 bytes) were downloaded from the API.
6. **Browser log:** no console errors, no failed requests and **no requests to any host other than 127.0.0.1**. The log is in `docs/ui-review/phase4-browser-log.json`.

**Conflicts** need two models that disagree, and only one real model exists. Conflicts were therefore exercised with the two stub detectors, both in pytest and in the browser test (e2e `phase4`): the conflict is created, both findings are disputed, one reviewer confirms and another rejects with reasons, and the conflict closes as resolved with documentation.

**Working stack:** the stack at http://127.0.0.1:5173 was restarted on the new code. Its existing project lists 12 observations and places 2 rasters.

## 4. Tests run and results

`scripts\check.ps1` was run after the final code change; all 7 checks pass.

| Check | Result |
| --- | --- |
| ruff check, ruff format | pass |
| pytest | **261 passed** (229 before this phase; the new tests are in `tests/test_review.py` and `tests/test_layers.py`) |
| tsc | pass |
| vitest | **95 passed** (78 before) |
| vite build | pass; MapLibre is a separate 1.03 MB chunk (273 kB gzip), loaded only on the map screen |
| Playwright e2e | **11 passed** (8 before; the new spec is `e2e/phase4.spec.ts`) |

**Backend coverage (pytest), against PostgreSQL/PostGIS and the real pipeline code:**

| Area | Tests |
| --- | --- |
| Review | Confirm, reject-needs-reason, reopen, invalid transitions, NO_CHANGE, relabel (category only), notes, profile required, audit events |
| Concurrency | Two reviewers on one revision → 409 (PT-18) |
| Immutability | The database refuses edits to the model output and to the history (T-11, CL-09) |
| Conflicts and duplicates | Conflict creation and documented resolution (T-07, CL-07); duplicate-copy linking; rejected findings excluded |
| Exports | Labelled CSV columns; GeoJSON with no pixel positions (PT-16, CL-13); JSON with history and provenance; empty project; missing project |
| Map placement | Layer placement and exclusion, including a missing-CRS raster (T-03, map half); AOI as its own layer |
| Tiles | Pixel-level placement checked against independently computed positions, for north-up and **30°-rotated** rasters; 204 outside the raster; refusals; the original unchanged after tiling |
| Coordinates | Round trip EPSG:32643 ↔ 4326, and x-first despite EPSG:4326's latitude-first axis order (PT-17, GEO-002); incomplete CRS refused; area-of-use warning |
| Migrations | Upgrade, downgrade, upgrade with the new tables |

**Frontend coverage (vitest):**

| Area | Tests |
| --- | --- |
| DMS | Formatting, carry, round trip within the displayed precision, and range checks (GEO-002) |
| Map page | Placed and excluded layers, visibility and opacity, the pinned point in decimal degrees, DMS and the layer CRS, layer details, empty project, AOI layer, layer not drawable yet |
| Review panel | Reason required before saving; the reviewed revision is sent; a 409 is explained and the record reloaded; relabel |
| Observations | Filters by current state, conflicts and text; review actions offered per state |
| Reports | Server exports; export failure shown with its code |
| Navigation | Map no longer marked planned |

**Simulated rather than real, stated plainly:**

- pytest and e2e analysis uses the **test-only stub detectors**. They prove plumbing and rules, not model behaviour.
- In vitest the **map component is replaced by a stand-in**, because jsdom has no WebGL. The real map was exercised in Edge by e2e `phase4` and by the walkthrough.
- The conflict path was exercised **only with stub models**: there is one real model, and running it twice gives identical results, which are linked as possible duplicates.
- The rasters used are **synthetic test GeoTIFFs**, made in memory by the tests or generated earlier for review. No real satellite or aerial image was available. The photograph is a standard public-domain test image.
- **Not tested:**
  - tile performance on large rasters (hundreds of MB);
  - rasters in geographic CRSs at high latitude;
  - antimeridian-crossing rasters;
  - rasters that are not 8-bit, on the map;
  - concurrency beyond two reviewers;
  - screen-reader use.

## 5. Screenshots and logs

- `docs/ui-review/screenshots-phase4/`: 13 screenshots from the real-model walkthrough. They cover the map (desktop and narrow), the pinned point and details, opacity and zoom, the ground review form and history, observations with review states, a possible duplicate, a relabel, the narrow observations view, exports and the satellite link.
- `docs/ui-review/phase4-browser-log.json`: console problems, failed requests, external requests and tile timings.
- `docs/ui-review/phase4-export.{csv,geojson,json}`: the exports from the walkthrough.
- The walkthrough can be re-run with:

  ```powershell
  $env:GEOPOC_REVIEW_SPEC = "phase4.spec.ts"
  pnpm exec playwright test --config playwright.ui-review.config.ts
  ```

None of these files is committed, and they should stay out of Git unless you approve.

## 6. Defects found and fixed during verification

- MapLibre's stylesheet made the map container `position: relative`, which collapsed it to 0 px. The canvas fell back to a default size and could not be clicked. Found by e2e; fixed, and the e2e test now checks the canvas fills its frame.
- The initial fit used a stale frame size, leaving the raster in a corner. The map now measures itself before fitting.
- The worker's capability probe delayed every job by 40–70 s on a cold start. This explains the previously unexplained e2e worker flake reported in the UI phase. The probe now runs in the background.
- The model list never refreshed after the probe finished. It now re-checks while the probe is running.
- `check.ps1` called `pytest.exe`, which Smart App Control now blocks. It now uses `python -m pytest`.
- The review endpoint could return an undocumented 400 for a malformed body. It is now documented (found by the OpenAPI contract test).

## 7. Still missing, and what each depends on

| Feature | Depends on |
| --- | --- |
| Satellite/raster analysis (detection, classification, change, measurements) | U-001: a model and labelled validation data for overhead imagery |
| Video analysis (frame sampling, findings timeline, association) | U-001 and U-010 (sampling interval; whether association is in scope) |
| Human CRS assertion for a raster without a CRS (blueprint endpoint 13) | Not built in this phase. It is a candidate for the next phase; the API and UI never propose a CRS. |
| Band and contrast controls; map display of rasters that are not 8-bit before their preview exists | Follow-up work |
| Basemap | U-013 (offline source and licence) |
| Review roles (who may verify) | U-009 |
| Controlled taxonomy for relabelling | U-015 |
| Report generation, versions, HTML and PDF; LLM narrative | Phase 5: U-002, U-008, D-014 |
| GeoPackage export | Phase 5 (exports with stored artifacts) |
| Editable settings, offline verification run | Phase 5 |
| 3D view | Phase 6, U-012 |

## 8. Known limitations, performance and follow-up

- **Tiles are rendered on request from the original.** For the 900 × 600 test raster this took 236 ms median per tile with a cold cache. Low-zoom tiles of a large raster without overviews read and decimate the whole visible extent, which could be slow for the 1 GB upload limit. This was not measured. Follow-up: build an overview or COG derivative per raster and record it as an artifact.
- The tile cache is per API process and in memory only (512 tiles); it is not shared or persisted.
- Rasters that are not 8-bit are drawn only after their preview has computed the display stretch. Until then the layer says why it cannot be drawn.
- The conflict rule compares bounding boxes only, at an IoU of 0.5 that is an operating setting, not a validated value. It applies only to the same image content, not across different photographs of the same scene.
- Relabelling is free text (U-015 is open) and is not checked against a taxonomy.
- The Observations screen loads up to 500 findings; beyond that it warns and points to the exports.
- In the Observations table at 1440 px, the last column ("Created") scrolls horizontally beside the detail panel.
- The local profile is still not authentication. Anyone using this computer can review under any profile.
- No accuracy claim is made for anything: the model is unevaluated and every score is labelled uncalibrated (G-04).

## 9. Dependencies and licensing

- **Added:** `maplibre-gl` 5.24.0 (BSD-3-Clause), pinned exactly. It is pure JavaScript with no native code, is bundled locally, and makes no network requests by itself. The map style references no online sources, fonts or sprites.
- **No backend dependency changes.** Tiles use rasterio (GDAL), PyProj and Pillow, which were already installed.

## 10. Files

- **New (backend):**
  - `migrations/versions/0004_phase4_review_conflicts_links.py`
  - `geopoc/review/{__init__,service,linking}.py`
  - `geopoc/api/routers/{observations,layers}.py`
  - `geopoc/geo/tiles.py`
  - `tests/test_review.py`, `tests/test_layers.py`
- **Changed (backend):**
  - `geopoc/db/models.py`, `geopoc/api/{main,schemas}.py`, `geopoc/api/routers/jobs.py` (observation listing moved out)
  - `geopoc/pipelines/ground_image.py` (linking after each run)
  - `geopoc/jobs/worker.py` and `geopoc/registry/manifest.py` (background probe)
  - `tests/{conftest,test_ground_pipeline,test_migrations,test_health}.py`, `tests/fixtures/manifest.stub.yaml` (adds `stub-detector-alt`)
  - `openapi.json`
- **New (frontend):**
  - `src/components/{MapCanvas,ReviewPanel}.tsx`
  - `src/lib/geo.ts`
  - `src/test/fixtures.ts`
  - tests: `src/lib/geo.test.ts`, `src/pages/map.test.tsx`, `e2e/phase4.spec.ts`
  - walkthrough: `review/phase4.spec.ts`
- **Changed (frontend):**
  - `src/api/{client.ts,schema.d.ts}`
  - pages: `src/pages/{MapPage,ObservationsPage,GroundAnalysisPage,ReportsPage,SatellitePage}.tsx`
  - `src/components/AppShell.tsx`, `src/lib/{jobs,queries}.ts`
  - tests: `jobs.test.tsx`, `screens.test.tsx`, `format.test.tsx`
  - `e2e/phase1.spec.ts`, `playwright.ui-review.config.ts`
  - `package.json`, `pnpm-lock.yaml`
- **Other:** `scripts/check.ps1`; `docs/architecture/decision-log.md` (Part H, decisions P4-01 to P4-14); this report.

---

# Phase 4 follow-up — georeferencing recorded by a person (2026-10-21)

Status: **complete and awaiting review.** Phase 5 and UI enhancements have not been started. Nothing has been committed. Decisions are P4-15 to P4-24 in Part I of the decision log.

## F1. What it does

A raster whose own metadata is missing or invalid can have its georeferencing recorded by a person. The work is split into three cases that are never confused:

| Case | What the file has | What is offered |
|---|---|---|
| A | A usable geotransform, but no usable CRS | **Assign a CRS** to the existing coordinates. The coordinates are kept exactly; nothing is reprojected or resampled. |
| B | A CRS, but no usable geotransform | **Assigning a CRS is not offered**: a CRS alone cannot place the raster. **Enter a geotransform** from a documented source: six GDAL-order numbers, or a world file. The source must be stated. |
| C | Coordinates that do not fit the chosen CRS, a degenerate or identity geotransform, or an unresolvable CRS | The proposal is **refused**, with the failed check named. Nothing is stored and the raster stays unplaced. |

Other rules:
- A raster whose own CRS and geotransform are valid is never overridden (409 `ALREADY_GEOREFERENCED`).
- **Revert** restores the file's metadata as a new version.
- Nothing is inferred from the filename, the project location, the image, or other data.
- Ground control points are not used. This is stated, not hidden.

## F2. Persistence, provenance and integrity

- **Migration `0005`:**
  - `georef_assertion` is append-only; a trigger refuses UPDATE and DELETE. Each version records:
    - asset, version number, operation and method;
    - CRS ID, name and WKT; geotransform; source of the numbers; world-file text;
    - mandatory reason;
    - **previous** and **resulting** metadata snapshots;
    - validation checks, corner mapping, transformation record and derived artifacts (none);
    - footprint;
    - the original's SHA-256, re-hashed from the stored file when the change is recorded;
    - profile, request ID and timestamp.
  - `raster_metadata` keeps what the file declares exactly as ingested. A new trigger refuses any change to it except the two pointer columns `georef_version` and `current_assertion_id`.
- **Concurrency:** recording requires `expected_version`, and the row is locked while a change is recorded. A stale version is refused with 409 `GEOREFERENCE_VERSION_CONFLICT`.
- **Confirmation:** recording requires `confirm: true` and a non-blank reason. The database also enforces the reason, and requires a source for entered geotransforms.
- **Original file:** never written. If the stored original no longer matches its upload checksum, the change is refused with `ORIGINAL_CHANGED`.
- **Audit:** every accepted change is in the audit log. Refused changes are logged too, as `georeference.refused` with the failed checks.
- **Not authentication:** local profiles attribute changes; they are not authentication or authorisation (U-009).

## F3. Validation

Validation is done on the server, with PROJ 9.5.1 (network off) and GDAL. It runs both when a change is checked and again when it is recorded.

| Check | Result when it does not pass |
|---|---|
| Reason present | failed |
| CRS resolves in the local registry and is 2D geographic or projected (`WGS84 + LCC`, unknown codes and vertical CRSs refused) | failed |
| Geotransform available and valid: six finite numbers, not the identity, non-zero area | failed |
| Geotransform rotated or sheared (the full affine transform is used) | warning |
| Raster outline transforms to valid longitude and latitude | failed |
| Units plausible (metres given a geographic CRS, or degrees given a projected one) | warning |
| Transformation accuracy stated | warning when PROJ states none |
| Inside the CRS's area of use | warning |
| A map tile actually renders from the original with the proposed metadata | failed on error; warning when the raster covers no pixel of the tile |

The review step shows each check together with the pixel-to-coordinate mapping of the four corners and the centre.

## F4. Map and tiles

- The layers endpoint, tile endpoint and asset views all use the **effective** version.
- Placement from a recorded version is labelled `user_asserted`: "Placed from recorded georeferencing" in the UI, with a link to the record.
- Tiles are rendered from the **unchanged original**, with the recorded CRS and geotransform passed explicitly.
- No tile made with older metadata is reused:
  - the in-memory cache key includes the version and record ID;
  - the tile URL carries `?v=<version>`;
  - the map adds a new source for each version.
- A failed check, or a failed test-tile render, leaves the previous valid version in use. A tile that fails to render returns 500 `TILE_RENDER_FAILED` for that tile only; the stored state is not touched.
- No basemap or external service was added.

## F5. UI

**Asset detail → Spatial placement panel** (in the existing design system):
- why the raster is unplaced;
- what the file declares, never changed, including missing fields and the reason;
- what is in use now;
- the operations, with unavailable ones disabled and the reason shown;
- a plain explanation of what each operation does and does not do;
- CRS search, using the existing registry picker;
- six labelled geotransform fields or world-file text, plus the source of the numbers;
- a mandatory reason;
- **Check this change**, which leads to a review step with every check and the corner table, then a confirmation checkbox and **Record as version N**;
- the result and a link to the map;
- the full history, showing previous → new CRS, geotransform and placement, the warnings at the time, who, when, and the original's checksum.

The "What is not known" panel now says that it describes the file as uploaded.

**Map:**
- each excluded raster has a "Record georeferencing…" link;
- placed layers show "recorded georeferencing, version N";
- layer details explain that the CRS or geotransform was recorded by a person and that its correctness is not established.

**Satellite Image Analysis:** shows the effective placement and links to the record.

## F6. Tests performed

All rasters in the tests are **synthetic**: GeoTIFFs written in memory with known coordinates (EPSG:32643, origin 500000, 3000000, 10 m pixels). Expected positions are computed independently with PyProj from the affine transform. **No real-world imagery was used**; none with a documented, missing CRS was available.

**Final `scripts\check.ps1` run** (after the last code change): see the summary at the end of this section.

**Backend (`tests/test_georeferencing.py`, 21 tests including parameterised cases), against PostgreSQL/PostGIS:**

| Area | What is tested |
|---|---|
| Case A | Assigning a CRS to existing coordinates: placement before and after, layer label, `?v=` tile URL, opaque tile at the independently computed centre, unchanged file metadata, audit entries, history snapshots, checksum, original bytes unchanged |
| Already valid | A file whose own georeferencing is valid is not overridden |
| Case B | A CRS alone refused without a geotransform; a geotransform needs a stated source; entering one with the file's CRS kept |
| CRS required | A geotransform without any usable CRS refused, then accepted with one |
| Invalid CRSs | `WGS84 + LCC`, `EPSG:999999`, vertical `EPSG:5773` |
| Malformed transforms | Identity, zero pixel size, degenerate; wrong length (422) |
| World files | Centre-to-corner conversion; unreadable world file refused |
| Case C | Metres assigned EPSG:4326: refused, nothing stored, refusal audited |
| Warnings | Units-plausibility and area-of-use warnings shown, not hidden |
| Rotation | 30°-rotated transform: every corner checked independently, plus tile alpha inside the raster and outside it within its bounding box |
| Concurrency | A stale version is refused |
| Cache | A new version re-renders the same tile address (no stale tile); revert restores the file state, tiles are refused again and the layer is excluded again |
| Failure recovery | A simulated GDAL failure during the test render is refused and the previous version stays in use, with tiles still served |
| Tampering | A tampered original is detected (`ORIGINAL_CHANGED`) and nothing is recorded |
| Immutability | The database refuses edits to the file metadata and to the history |
| Access | Non-raster refused, profile required, 404 |

**Other backend changes:** the migration test covers 0005 (up, down, up); the contract test covers the new endpoints. It found one undocumented 400, which is now documented.

**Frontend (vitest):**
- 6 panel tests: unplaced explanation and file metadata; the full assign → check → review → confirm → record flow with the exact request bodies; a CRS alone not offered and the geotransform fields; a failed validation that cannot be recorded; a version-conflict message and reload; nothing offered for an already-valid raster.
- Map tests: the record link only for rasters; the recorded-placement label.

**Browser (`e2e/phase4b.spec.ts`, 3 tests, real API, database and worker in Edge):**
- case A end to end, including a real `?v=1` tile, the original's SHA-256 unchanged, and no external requests;
- case B by world file;
- a refused invalid CRS, then revert.

**Walkthrough with screenshots (`review/georeferencing.spec.ts`)** on the scratch stack:
- the real review file `scan_without_crs.tif` has **neither a CRS nor a geotransform**. It is shown with "assign a CRS" unavailable, and nothing was recorded for it;
- the assignment itself was done on a clearly named synthetic raster, `synthetic_utm_coordinates_no_crs.tif`;
- 10 screenshots, with no console problems, no failed requests and no external requests;
- the original's SHA-256 was confirmed unchanged.

The walkthrough's integrity check first reported false. That was a bug in the script, which compared the download against the wrong uploaded file; with the bug fixed it reports true. The stored and uploaded hashes were identical throughout.

**Working stack:**
- backed up to `D:\geopoc-cache\backups\geopoc-before-0005-20261021-134447.dump` (verified with `pg_restore -l`);
- migrated to 0005 and restarted;
- checked read-only: 9 assets, 12 observations, 0 revisions and 0 georeferencing versions;
- your rasters are unchanged, and nothing was recorded against your data.

Screenshots are in `docs/ui-review/screenshots-georeferencing/` and the log is `docs/ui-review/georeferencing-browser-log.json`. Both are uncommitted.

## F7. Limitations

- **No real-world validation.** Correctness was checked on synthetic rasters only.
- **Correctness of the location is not established.** The checks show that the values are usable and consistent, not that they are right: a wrong but valid CRS (for example a neighbouring UTM zone) passes, with an area-of-use warning only when the raster falls outside that CRS's area.
- **Not supported:**
  - ground-control-point georeferencing;
  - reading `.aux.xml`, `.prj` or `.tfw` sidecar files automatically (world-file text is pasted);
  - vertical CRSs, compound CRSs and custom WKT definitions (registry codes only);
  - correcting a raster whose own valid metadata is believed to be wrong.
- **Tile cost.** The test-tile render adds one tile render to each check and each recording, and recording re-hashes the original. For large rasters, near the 1 GB limit, both take noticeable time. This was not measured.
- **Asset flags.** `CRS_USER_ASSERTED` exists in the flag registry for future derived results. The asset's own flags still describe the file as uploaded; the UI says so.

**Result of the final run (after the last code change, including the wording fix):** all 7 checks pass — ruff check and format, pytest **284 passed**, tsc, vitest **102 passed**, vite build, Playwright **14 passed**.
