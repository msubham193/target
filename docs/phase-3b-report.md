# Phase 3b report: non-AI raster tools

Date: 2026-10-21. Scope: owner decisions K-01 to K-07 (decision log Part K); the implementation plan's Amendment A1. **This phase stops at the Phase 3b gate.** No AI inference, model download, dataset download or Phase 5 work has been started. Nothing has been committed.

## 1. Summary

The five approved capabilities are implemented, tested and exercised in the running application. Each one is labelled with what it is and is not:

| # | Capability | Where | Key rule |
|---|---|---|---|
| 1 | Band selection and contrast stretch | Satellite Image Analysis; map layer display | Display only, never stored. Bands are shown with the file's own descriptions ("as the file declares", or "no description in the file"); nothing is inferred. |
| 2 | Pixel values and band statistics | Satellite page; map pin | Values are read from the original, with nodata, scale/offset and units only as declared. Statistics are exact up to 16 M pixels per band, otherwise a labelled sample. |
| 3 | Distance and area measurement | Satellite page (pixels → ground); map (lon/lat) | Geodesic on the CRS ellipsoid, plus planar for projected CRSs. **Unavailable** without valid georeferencing, giving pixel units only. Recorded (user-asserted) georeferencing is flagged. |
| 4 | Display copies for large rasters | Satellite page; automatic after upload | A lossless COG with overviews, kept only after 25 full-resolution windows and all metadata match the original. Used for drawing only. |
| 5 | Pixel comparison (B − A) | Satellite page | Same grid only (CRS, geotransform and size); no resampling. The result is a **derived asset** with append-only lineage. It is presented as a pixel difference, not as change interpretation. |

Also delivered:

- the sidecar plan for `.tfw`, `.prj` and `.aux.xml`: [sidecar-metadata-plan.md](sidecar-metadata-plan.md), plan only;
- the SAM 2.1 approval brief: [sam2.1-approval-brief.md](sam2.1-approval-brief.md), with nothing downloaded;
- the imagery inventory and benchmark plan: [imagery-inventory-and-benchmark-plan.md](imagery-inventory-and-benchmark-plan.md);
- the video policy, recorded as K-06;
- plan Amendment A1 and decision-log Part K, appended (historical reports unchanged).

## 2. Changes

### Database (migration 0006, `0006_phase3b_raster_tools`)

- New job kinds: `statistics`, `display_copy`, `compare`.
- `asset.origin` accepts `derived` (it was `upload` only).
- New table `asset_lineage`: child, parent, relation `pixel_difference_of`, role a/b, tool and version, parameters, the parent's SHA-256 and georeferencing version, and the job. It is **append-only, enforced by a trigger**.
- The downgrade removes only rows of the new kinds.

### Backend (`services/api/geopoc`)

- `geo/rasterstats.py`: per-band statistics (min, max, mean, std, percentiles 0.5–99.5, 64-bin histogram, valid/nodata/non-finite counts). Method `exact` or `sample`, with a note. Complex types are refused.
- `geo/display.py`: resolves a display request (bands; stretch: default, none, minmax, p0.5-99.5, p1-99, p2-98, p5-95, manual) into ranges, with a plain-language description. The error codes are DISPLAY_INVALID (422) and DISPLAY_NOT_READY / DISPLAY_NOT_AVAILABLE (409).
- `geo/displaycopy.py`: decides when a copy is needed (larger than 4096 px on a side and no overviews of its own), then builds it (COG, DEFLATE, AVERAGE overviews, 512 px blocks) and verifies it.
- `geo/measure.py`: measurement on the map (WGS 84) and on rasters (affine → CRS → geodesic, plus planar). Refuses antimeridian crossings and self-intersecting outlines; returns `unavailable` without georeferencing.
- `geo/compare.py`: the alignment check (with reasons) and a block-wise B − A computation (float32/64, NaN nodata).
- `geo/tiles.py`: tiles and image rendering accept display parameters and recorded georeferencing. **Fix:** a manual stretch was ignored on 8-bit bands.
- `rastertools.py`: the three job handlers. The display copy re-checks the original's SHA-256 before replacing anything. Comparison output is ingested as a derived asset, with `DERIVED_ASSET` and, where applicable, `CRS_USER_ASSERTED`.
- `ingestion/raster.py`: records declared per-band metadata, overview levels and block shape.
- `ingestion/service.py`: queues statistics (and a display copy when needed) for new rasters. Existing assets are changed only on request (P3b-07).
- API (`api/routers/rastertools.py`, `layers.py`): `GET /assets/{id}/raster-tools`, `POST /assets/{id}/statistics`, `POST /assets/{id}/display-copy`, `GET /assets/{id}/display`, `GET /assets/{id}/render.png`, `GET /assets/{id}/pixel`, `POST /measurements`, `GET /assets/{id}/alignment/{other}`, `POST/GET /projects/{id}/comparisons`. Map tiles take `bands`, `stretch`, `low` and `high`.
- Configuration: `statistics_exact_max_pixels` (16,000,000), `display_copy_min_side` (4096), `compare_max_pixels` (400,000,000).

### Frontend (`apps/web/src`), using the existing design system with no visual redesign

- `components/RasterTools.tsx`: band and contrast controls, band statistics with histograms, the display copy panel, the measurement result.
- `components/RasterWorkbench.tsx`: an image viewer with Inspect pixel, Measure distance and Measure area tools, the comparison panel and the lineage panel.
- `pages/SatellitePage.tsx`: the tools on the raster page. The planned panel is renamed "AI raster analysis", still stating that no model is installed.
- `pages/MapPage.tsx`, `components/MapCanvas.tsx`: per-layer display settings, Measure distance and area tools, and stored raster values at a pinned point.
- `lib/geo.ts`: length and area formatting.

### Tests and tools

- `tests/test_raster_tools.py` (new): statistics (exact, sampled, nodata, scale/offset, complex refused), display resolution, pixel reads, measurements (known answers, unavailable, user-asserted), display copy build and verification, comparison (alignment refusals, values, nodata, lineage, derived name), and originals unchanged.
- `tests/bench_display_copy.py`: the large-raster benchmark (not collected by pytest).
- `src/components/rastertools.test.tsx` (new) and additions to `map.test.tsx` and `geo.test.ts`.
- `e2e/phase3b.spec.ts` (4 tests) against the real API, database and worker.
- `review/phase3b.spec.ts`: the screenshot walkthrough (§5).
- Updated for the new jobs and migration: test_previews, test_ground_pipeline, test_health, test_migrations, conftest.

### Dependencies

**None added.** Everything uses packages that were already installed (rasterio/GDAL, pyproj, NumPy, MapLibre GL).

## 3. Test results

`scripts\check.ps1`, final run on 2026-10-21, after every fix in §5:

| Check | Result |
|---|---|
| Backend lint (ruff check) | PASS |
| Backend format (ruff format) | PASS |
| Backend tests (pytest, PostgreSQL + PostGIS) | **PASS: 315 passed**, including `test_raster_tools.py` (18 test functions, some parametrised) |
| Frontend type-check (tsc) | PASS |
| Frontend unit tests (vitest) | **PASS: 114 passed** in 10 files, including `rastertools.test.tsx` (9) |
| Frontend build (vite) | PASS |
| Browser end-to-end (Playwright, real API, DB and worker) | **PASS: 18 passed**, including `phase3b.spec.ts` (4); Phases 1–4 unchanged |

The Phase 4 map, audit, georeferencing, observation and export suites all pass unchanged (K-07).

## 4. Performance measurements

Synthetic benchmark, `tests/bench_display_copy.py`, on this machine (2026-10-21). Best of 3 per tile; disk cache warm after the first run; the benchmark files were deleted afterwards.

Input: a 12000 × 12000 × 3 uint8 GeoTIFF, striped, uncompressed, no overviews.

| Measure | Original | Display copy |
|---|---|---|
| Size | 412.1 MB | 278.0 MB (lossless DEFLATE) |
| Build and verify time | — | 23.85 s |
| Overview levels | none | 2, 4, 8, 16, 32 |
| Tile z12 (whole raster in about one tile) | 3552.9 ms | 116.7 ms |
| Tile z14 | 564.6 ms | 134.4 ms |
| Tile z17 (full resolution) | 82.4 ms | 81.3 ms |

The walkthrough's browser timings (small 900 × 600 rasters, review stack, first-request times including the cache warming):

| Request | Count | Median | Max |
|---|---|---|---|
| `render.png` (satellite image view) | 11 | 84 ms | 638 ms |
| Map tiles (reprojected from the original) | 24 | 541 ms | 898 ms |

Tile times on these small rasters are dominated by first-request reprojection on a cold cache, with several tiles requested in parallel by the map. They were not optimised further in this phase.

These figures are indicative only. They depend on the disk, the cache state and the raster's layout, and **no real-world imagery has been measured** (§6).

## 5. Application walkthrough

`review/phase3b.spec.ts` ran on a scratch review stack (its own database, ports 8011/5175) with synthetic rasters. Eleven screenshots are in `docs/ui-review/screenshots-phase3b/` (not committed; the screenshot policy is unchanged), and the browser log is in `docs/ui-review/phase3b-browser-log.json`.

| Step | Result |
|---|---|
| 16-bit, 4-band raster: default display; bands 4-2-3 with a 2–98 % stretch | Display line reads "Showing bands 4, 2, 3: stretched between the 2 and 98 percentiles of each band (exact statistics)". Band 4 is shown with its declared description; bands 1–3 as "no description in the file". |
| Pixel values | Stored values per band, with the pixel position and, where georeferenced, lat/lon |
| Area on a georeferenced raster | Result labelled valid: geodesic on the CRS ellipsoid, plus planar |
| Distance on an unreferenced scan | "No ground measurement — unavailable", pixel length only |
| Display copy on request | Built and verified ("… windows identical to the original") |
| Comparison with an unreferenced raster | Refused, with the reason |
| Comparison of the same-grid pair, band 4 | Derived raster: min/max 0 / 900, lineage to A and B, purpose recorded |
| Map: geodesic distance; pinned point | Valid WGS 84 measurement; stored values from all four placed rasters at the pin |
| Narrow screen (390 px) | All tools stack in one column; no horizontal scroll |

The browser log shows **0 external requests**. The 409 responses logged are `DISPLAY_NOT_READY`: the page asks for the display before a just-uploaded raster's preview and statistics exist, then repeats the request once they do. These 409s show as console errors in the browser, but they are expected and harmless. One earlier run logged a single `ERR_CONNECTION_TIMED_OUT` without a URL. After request-failure logging was added, two further full runs showed none, so it was not reproduced; it is listed here rather than assumed away.

Defects found by the walkthrough and fixed:

- **The derived raster name ended in `.tif.tif`.** It is now built from the file stems ("difference after minus before.tif"), with a test.
- **Walkthrough script errors (not application defects):**
  - The refusal case used two rasters that really do share a grid; it now uses the unreferenced scan.
  - The synthetic "after" raster wrapped around at 65535; it now saturates.
  - The output buffer was too small.
  - The narrow-screen step used the hidden navigation.
  - Full-page captures of scrolled pages drew the sticky header mid-page.

## 6. Limitations and remaining gaps

- **Synthetic data only.** No real-world satellite or aerial imagery exists on this machine (inventory, §1 of the benchmark plan). Correctness is shown by known-answer tests on synthetic grids. Behaviour on real products (Sentinel-2, Landsat, VHR orthophotos, rotated or very large rasters, unusual nodata) has not been observed.
- **Default stretch on non-8-bit rasters** uses the preview's 2–98 % sample, and the display says so. A percentile stretch based on exact statistics is one click away.
- **Statistics for very large rasters** are a labelled nearest-neighbour sample above 16 M pixels per band, not exact.
- **The display copy** covers drawing only. Statistics, pixel values, measurements and comparisons read the original, so they are not faster for huge rasters. The comparison size limit is 400 M pixels.
- **Comparison** needs an identical grid; there is no reprojection or resampling, by design. Only B − A of one band each; no ratios or indices.
- **Measurement:** no elevation or terrain, so distances are on the ellipsoid. Map lines crossing the antimeridian are refused.
- **Sidecars** (`.tfw`, `.prj`, `.aux.xml`): planned at the gate; implemented afterwards for georeferencing (§9). Band metadata from them is still not applied.
- **Existing rasters** uploaded before 0006 get statistics and display copies only when a person asks.
- **Not started, as instructed:** AI inference, model or dataset downloads, video sampling, UI enhancement, Phase 5.

## 7. Working stack

- Backup before upgrading: `D:\geopoc-cacheackups\geopoc-before-0006-20261021-160554.dump` (custom format, 84 KB, 28 table-data entries; SHA-256 `4f8fa455…99d1ab0f`).
- Upgraded with `scripts\stop.ps1` then `scripts\dev.ps1`; the schema revision is now **0006**.
- Row counts are identical before and after for all user data: 9 assets, 8 blobs, 12 observations, 16 artifacts, 19 audit events, 14 jobs, 1 project and 2 profiles. Only the worker heartbeat changed (+1).
- All **8 stored originals** match their recorded SHA-256 after the upgrade.
- Health: status ok, worker running, online access off, PROJ network off, bound to 127.0.0.1.

## 8. For the gate review

Owner decisions needed to continue:

1. Accept Phase 3b's non-AI raster tools, or list changes.
2. SAM 2.1: approve or decline the items in sam2.1-approval-brief.md §9 (recommended: the code at a pinned commit, plus the tiny and small checkpoints, 340.4 MB).
3. Imagery: categories and sources for the evaluation set (benchmark plan §5).
4. Whether to implement the sidecar plan next, or after the AI evaluation.

---

## 9. Follow-up: sidecar metadata (2026-10-21)

After the Phase 3b gate, the owner accepted the five non-AI raster tools for the local PoC. They also approved sidecar discovery with review-and-confirm for `.tfw`, `.prj` and `.aux.xml`. Full details, rules and the differences from the earlier plan are in [sidecar-metadata-plan.md](sidecar-metadata-plan.md). Decisions: P3b-08 to P3b-13 in the decision log.

### 9.1 What was built

- **Upload with a raster, or attach to one.** World files (`.tfw`, `.tifw`, `.tiffw`, `.wld`), `.prj` and `.aux.xml` can be chosen together with their raster on Upload & Ingestion, or attached on the raster's page.
  - Pairing is by name, case-insensitive. Orphan and ambiguous sidecars are refused and audited.
  - Files are stored unmodified and checksummed; nothing is applied by uploading.
- **Review on the asset page** ("Sidecar files", under Spatial placement). The panel shows:
  - what each file says, its problems, and content present but not used;
  - a field-by-field comparison with the raster file and with the state in use;
  - the review state: none, file valid, proposal, conflict, not enough, or already in use;
  - the proposed change.
- **Confirm.** A required reason; "Check this change" runs the existing georeferencing validation plus the sidecar checks; an explicit confirmation; then "Record as version n".
  - The result is an ordinary append-only georeferencing version with method `sidecar_geotransform` or `sidecar_crs`, and the files used are listed with their checksums.
  - Tiles, the map, the audit and the history follow, as for georeferencing entered by hand.
- **Never applied:**
  - valid raster-owned metadata is not overridden; conflicts are shown;
  - a world file alone does not set a CRS;
  - a `.prj` alone cannot place a raster without coordinates;
  - a CRS definition is used only if the registry resolves it completely;
  - `.aux.xml` band metadata is shown but not applied;
  - GCPs, RPCs and unknown elements are listed and not used.
- **GDAL PAM disabled** (`GDAL_PAM_ENABLED=NO`) in every process, so GDAL never reads or writes `.aux.xml` on its own.

### 9.2 Changes

| Area | Files |
|---|---|
| Database | `migrations/versions/0007_sidecar_metadata.py`: the `sidecar` table (append-only trigger); georeferencing methods `sidecar_crs` and `sidecar_geotransform`; the source constraint extended; a downgrade refused once sidecar-based versions exist. `db/models.py`: `Sidecar`. |
| Backend | `geo/sidecars.py` (naming, pairing, parsers, CRS resolution, review); `api/routers/sidecars.py` (upload, attach, review, validate, apply); `geo/georeference.py` (`apply` accepts the method, the provenance and extra checks; validation is unchanged); `api/routers/assets.py` (ingestion rules list the sidecar extensions; the upload-rejection helper takes an audit action); `api/schemas.py`; `api/main.py`; `geopoc/__init__.py` (`GDAL_PAM_ENABLED=NO`). |
| Frontend | `components/SidecarPanel.tsx` (new); `pages/IngestionPage.tsx` (sidecars in the upload queue, uploaded after their raster); `pages/AssetDetailPage.tsx`; `components/GeoreferencingPanel.tsx` (shared check list and corners table, sidecar method labels, sidecar files in the history, a unique key per check); `lib/ingestion.ts` (sidecar naming and size checks); `api/client.ts`, `api/schema.d.ts`, `services/api/openapi.json` (regenerated). |
| Tests | `tests/test_sidecars.py`; `src/components/sidecars.test.tsx`; `e2e/phase3c.spec.ts`; `review/phase3c.spec.ts`; `tests/conftest.py` (table list); `tests/test_health.py` (revision 0007). |

**Dependencies: none added.** `.aux.xml` uses the standard-library XML parser (Expat 2.6.0) and refuses DOCTYPE and ENTITY declarations. The reasoning is in decision P3b-13.

### 9.3 Test results

`scripts\check.ps1`, final run on 2026-10-21, after all fixes:

| Check | Result |
|---|---|
| Backend lint and format (ruff) | PASS |
| Backend tests (pytest, PostgreSQL + PostGIS) | **PASS: 372 passed** (was 315), including `test_sidecars.py` with 52 tests and the OpenAPI contract tests for the new endpoints |
| Frontend type-check (tsc) | PASS |
| Frontend unit tests (vitest) | **PASS: 122 passed** in 11 files (was 114), including `sidecars.test.tsx` with 8 tests |
| Frontend build (vite) | PASS |
| Browser end-to-end (Playwright, real API, DB and worker) | **PASS: 20 passed** (was 18), including `phase3c.spec.ts` with 2 tests. The Phase 3b raster-tools suite (`phase3b.spec.ts`, 4 tests) and the Phase 4 suites (map, review, audit, georeferencing, observations, exports) pass unchanged. |

**Defects found and fixed before this run:**

- A version conflict was reported as "nothing to record". The version is now checked before the review is recomputed.
- The list of sidecars used was in upload order; it is now ordered geotransform source, then CRS source.
- The validate endpoint did not document its 400 response; the contract test caught it.
- React logged a duplicate key in the check list, which has one sidecar check per file.
- On a narrow screen, the comparison table wrapped geotransforms one number per line; it now scrolls inside its frame.

**Synthetic tests only.** Every raster and sidecar in these tests was generated, and each expected coordinate was computed independently in the test. **No real-world imagery and no real supplier sidecar files have been verified**; there are none on this machine.

### 9.4 Browser walkthrough

`review/phase3c.spec.ts` ran on the scratch review stack, with eight screenshots in `docs/ui-review/screenshots-sidecars/` (not committed) and a log in `docs/ui-review/sidecars-browser-log.json`. The steps:

1. Nine files uploaded together: three rasters, two world files, two `.prj` files, one `.aux.xml` and an orphan `.prj`.
2. Review and confirmation of `scan.tif` from its world file and `.prj`.
3. An unresolvable custom LCC `.prj` shown as "Not enough to place the raster".
4. A valid raster with a disagreeing `.aux.xml`: the conflict is shown, nothing is offered, and the RPC and GCP content is listed as not used.
5. The map with `scan.tif` placed.
6. The narrow (390 px) layout.

**Browser log:** 0 external requests. The only error is the intended 422 for the orphan `stray.prj` ("No raster in this project matches this sidecar's name").

### 9.5 Limitations and remaining gaps

- Synthetic data only (above). Non-UTF-8 `.prj` files are refused, not guessed.
- The strict CRS rule refuses some valid definitions, for example NZTM (EPSG:2193) written as ESRI WKT1. The person can still choose the CRS from the registry in Spatial placement.
- Band metadata from `.aux.xml` is not applied; applying it needs its own reviewed record.
- Not used: GCPs, RPCs, `.ovr`, `.msk`, `.vrt` and old `.aux` files.
- Sidecars cannot be deleted (append-only); a later upload of the same kind supersedes an earlier one.
- Exports do not yet list the sidecars behind a version; the history and the audit do.
- Pairing by name is within one project.

### 9.6 Working stack

- Backup before upgrading: `D:\geopoc-cacheackups\geopoc-before-0007-20261021-170056.dump` (88 KB, 29 table-data entries; SHA-256 `09eec2d1…1601ffbe`).
- Upgraded with `scripts\stop.ps1` then `scripts\dev.ps1`; the schema revision is now **0007**, and the `sidecar` table is empty.
- Row counts are identical before and after for all user data; only the worker heartbeat changed.
- All **8 stored originals** match their recorded SHA-256.
- Health: status ok, worker running, online access off, PROJ network off, bound to 127.0.0.1.

### 9.7 Gate

Stopped here for owner review. SAM 2.1 compatibility testing, AI inference, video analysis, UI enhancement and the original Phase 5 have not been started. Nothing has been downloaded or committed.
