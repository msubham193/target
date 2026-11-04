# OSM and ESM implementation: assessment

Date: 2026-10-31. Status: **assessment only** when written. No source file, database, migration, setting or service was changed
to produce it.

> **Status update, 2026-11-04.** This is the assessment as it stood on 2026-10-31. Much of what it lists as missing has been
> built since, so read sections 1 and 16 as history. What is implemented and covered by (synthetic) automated tests, and what is
> not, today:
>
> | Finding in this document | Now |
> |---|---|
> | Implicit, unclassified datum transformations (item 4) | **Implemented and tested.** A planner classifies every operation (exact, approximate, ballpark, unavailable); one converter serves every path ([crs-conversion-coverage.md](crs-conversion-coverage.md)); ballpark is recognised structurally and always blocked; a shift worse than 10 m or with no stated accuracy needs acknowledgement ([transformation-acknowledgement.md](transformation-acknowledgement.md)). The default Kalianpur pick (999 m) is no longer applied silently. |
> | No map-sheet concept, no non-TIFF inputs (item 1) | **Implemented and tested** for TIFF, PNG and JPEG scans and PDF (stored, structure scanned, not rendered) ([map-sheets.md](map-sheets.md)). |
> | No control-point workflow (item 2) | **Implemented and tested**: ground-metre tolerance, leave-one-out, exact-fit warning, evidence schema 2. |
> | No single status model (item 6) | **Implemented and tested** (CRS assessment, eight statuses). |
> | Alignment check | **Implemented and tested** (footprints and documented accuracies; it does not measure feature alignment from pixels). |
> | Antimeridian, axis orientation, PROJ/GDAL disagreement | **Hardened in Phase 2** and tested ([crs-assessment-and-transformations.md](crs-assessment-and-transformations.md)). |
> | Custom CRS definitions such as a polyconic or Lambert conic on an Everest ellipsoid (item 3) | **Not implemented.** `crs_definition` is still empty. No ESM CRS, datum, ellipsoid, projection or transformation is defined or guessed. |
> | Layer ordering, compare modes, native-CRS readout (item 5) | Not re-checked in Phase 2. |
> | Real OSM or ESM sheets | **Unverified.** None has been supplied. Every test fixture is synthetic. An offline harness exists ([map-sheet-validation.md](map-sheet-validation.md)) and reports NOT VALIDATED. |
> | Live browser verification | Playwright specs exist but were not run in Phase 2. |
>
> Migration references below are also stale: migration `0010` is now `derived_index_job_kind` (NDVI Studio), written and tested
> on scratch databases but **not applied**; the working database is at `0009`. Phase 2 needed **no migration** and `0011` does
> not exist.

Scope of the request: make Open Series Map (OSM) and Everest Series Map (ESM) sheets the primary focus (import, inspect
metadata, resolve the CRS, georeference where needed, transform coordinates, overlay on satellite imagery), keep the 3D
elevation viewer as a secondary feature, and keep Defence Series Maps and restricted defence mapping workflows **out of scope**.

## 1. Summary

**The existing application is a good base, and its rules already match the new requirements.** It never assumes a CRS, keeps
originals unmodified, records georeferencing as append-only versions with validation and provenance, and renders map tiles
by real reprojection from the raster's own CRS and full affine transform. A GeoTIFF with a valid CRS can already be uploaded,
placed and overlaid today, and a TIFF with a world file or `.prj` can already be reviewed and georeferenced through the sidecar
workflow.

**What is missing is mostly the map-sheet half of the problem:**

1. There is no "map sheet" concept: only `ground_image`, `overhead_raster` and `video` exist, and rasters must be `.tif/.tiff`.
   No PDF, JPEG/PNG scan or vector file can be uploaded.
2. Georeferencing a scan can only be done by **typing a CRS code and six affine numbers or a world file**. There is no
   ground-control-point (GCP) workflow, even though the file's GCP count is read and shown as "present, not used".
3. A CRS must be an **EPSG/ESRI registry code**. A custom CRS (for example a polyconic or Lambert Conformal Conic on an Everest
   ellipsoid with explicit parameters) cannot be defined, although an empty `crs_definition` table was reserved for it.
4. **Datum transformations are implicit.** Every conversion lets PROJ and GDAL choose their default operation and records only
   a text definition. My probes show this is unsafe for the Everest family (section 6): PROJ silently falls back to a
   *ballpark* shift with unknown accuracy for a CRS that has no datum-shift information, and for Kalianpur/Indian datums
   it has several registered shifts of 1 m to 999 m stated accuracy. The default pick for Kalianpur 1962 / UTM 43N moved a
   test point by about **279 m** relative to the better-documented operation. No grid files are installed and the network is off.
5. The Map & Analysis screen has per-layer visibility and opacity and a live pointer readout, but **no layer ordering, no
   compare mode, no native-CRS coordinate readout and no alignment-check tool**.
6. The statuses requested in section 8 (georeferenced, CRS identified/missing/ambiguous, transformation
   available/unavailable, georeferencing required, validation failed) exist only as scattered flags. There is no single
   assessment that combines them.

**Recommended next phase: Phase 1 below, "CRS assessment and transformation transparency".** It needs **no migration and writes
no data**. It adds a read-only per-raster CRS assessment (datum, projection, parameters, units, status) and a transformation
classifier that labels every operation as exact, approximate (with stated accuracy), ballpark or ambiguous, and it makes the
existing footprint, point-conversion and tile paths report which operation they used. This closes the one gap that could cause
silent mis-registration and is a prerequisite for every later phase.

## 2. What was inspected

Everything below was read; nothing was executed against the working database or the running services.

| Area | Files and sources |
|---|---|
| Architecture | `README.md`, `docs/runbook.md`, `docs/implementation-plan.md`, `docs/architecture/*` (blueprint, decision log, traceability, observation schema), phase reports, `docs/acceptance-checklist.md`, `docs/3d-terrain-viewer.md`, `docs/earth-engine-feasibility.md`, `docs/sidecar-metadata-plan.md` |
| Upload and ingestion | `services/api/geopoc/api/routers/assets.py` (streamed upload, extension and signature rules), `ingestion/rules.py`, `ingestion/service.py`, `ingestion/raster.py`, `storage/store.py`, `config.py` |
| Raster metadata | `ingestion/raster.py` (`inspect_raster`), `db/models.py` (`RasterMetadata`), `api/routers/assets.py` output mapping |
| CRS and transformation | `geo/crs.py`, `api/routers/crs.py`, `api/routers/layers.py` (`/crs/transform`), `geo/georeference.py`, `geo/measure.py` (listing), `api/routers/rastertools.py` (pixel lookup, measure, alignment) |
| Georeferencing and sidecars | `geo/georeference.py`, `api/routers/georeferencing.py`, `geo/sidecars.py`, `api/routers/sidecars.py`, `db/models.py` (`GeorefAssertion`, `Sidecar`, `CrsDefinition`, `AssetLineage`) |
| Map rendering and overlay | `geo/tiles.py`, `geo/display.py`, `geo/displaycopy.py`, `api/routers/layers.py`; frontend `pages/MapPage.tsx`, `components/MapCanvas.tsx`, `components/GeoreferencingPanel.tsx`, `components/SidecarPanel.tsx`, `components/RasterWorkbench.tsx`, `components/CrsPicker.tsx`, `pages/SatellitePage.tsx`, `App.tsx`, `components/AppShell.tsx` |
| 3D and satellite | `api/routers/terrain.py`, `geo/terrain.py` (listing), `geopoc/satellite/*` (Earth Engine prototype, disabled) |
| Database and migrations | `migrations/versions/0001` to `0009` (constraints and triggers), `db/models.py` |
| Tests | `tests/media.py` (synthetic fixtures), `test_crs.py`, `test_layers.py`, `test_georeferencing.py`, `test_sidecars.py` (listing), the test inventory (about 498 backend test functions, about 371 frontend cases, 21 browser specs) |
| Installed libraries (read-only probes) | `pyproj 3.7.1` with `PROJ 9.5.1`, `rasterio 1.4.3` with `GDAL 3.9.3`, the local `proj.db` (opened read-only), the installed GDAL driver list, whether vector/PDF Python packages are installed |
| Probe scripts (scratch only, outside the repository) | `...\scratchpad\proj_probe.py`, `...\scratchpad\proj_probe2.py` (outputs summarised in section 6) |

Not done: no tests were run (the suites are unchanged), no service was restarted, no migration was run, nothing was uploaded,
no real or sample OSM/ESM sheet was available or used.

## 3. How the application works today (relevant parts)

```
upload (raw body, streamed, SHA-256) ──► signature + extension check ──► inspect_raster (rasterio/GDAL)
        │                                                                     │
        ▼                                                                     ▼
 blob (original, immutable, by hash)            asset + raster_metadata (file's own CRS/geotransform, immutable by trigger)
        │                                                                     │
        │                          georef_assertion (append-only versions: assign_crs / set_geotransform / revert)
        │                          sidecar (world file, .prj, .aux.xml: evidence to review, then proposed as a version)
        ▼                                                                     ▼
 GET /assets/{id}/tiles/{z}/{x}/{y}.png  ◄── georeference.effective(): file state or latest recorded version
   rasterio reproject(source CRS, full affine) → EPSG:3857 PNG tiles, cached per (asset, version, display)
        ▼
 GET /projects/{id}/layers ──► MapPage / MapCanvas (MapLibre, local style, no online source)
```

Key properties that the new work must keep:

- `raster_metadata` file columns are immutable (trigger `tr_raster_file_metadata_is_immutable`); recorded georeferencing is
  `georef_assertion` rows with trigger `tr_georef_assertion_append_only`. Tiles are cached under a key that includes the
  georeferencing version, so a new version never reuses an old tile.
- Placement is `trusted` (file's own metadata) or `user_asserted` (recorded by a person). `CRS_USER_ASSERTED` propagates
  to derived rasters and measurements.
- Proposals are validated before storage with named checks (`CRS_RESOLVES`, `GEOTRANSFORM_VALID`, `UNITS_PLAUSIBLE`,
  `COORDINATES_FIT_CRS`, `TRANSFORMATION_AVAILABLE`, `WITHIN_AREA_OF_USE`, `TILE_RENDERS`, `ORIGINAL_UNCHANGED`) and refused
  with the reasons; a refusal is audited and stores nothing.
- All of this runs locally. PROJ network access is disabled in code (`pyproj.network.set_network_enabled(False)`).

## 4. Findings by requirement

Legend: **Supported** = works today and is tested; **Partial** = some of it works; **Missing** = not present.

### 4.1 OSM requirements (request section 4)

| Requirement | State | Evidence and gap |
|---|---|---|
| Georeferenced GeoTIFF | **Supported** | `inspect_raster` reads CRS, geotransform, bounds, nodata, bands; footprint transformed to EPSG:4326; tiles by reprojection; rotated/sheared transforms handled (`test_layers.py`). |
| TIFF with world file | **Supported** | `geo/sidecars.py` accepts `.tfw/.tifw/.tiffw/.wld`, converts the pixel-centre convention to GDAL's corner, requires a CRS (a world file has none), and records the result as a versioned `georef_assertion` after a reason and confirmation. Pairing by file name or explicit attach. |
| `.prj` / `.aux.xml` | **Partial** | `.prj` is accepted only if exactly one registry CRS matches completely. A WKT for an Everest-based CRS that is not in the registry is *refused* (correct), but there is no way to then register it (see 4.3). |
| PDF map sheets | **Missing** | `.pdf` is not an accepted extension; no signature rule (`%PDF`). GDAL's PDF driver is listed by rasterio but it has never been exercised here; whether it can render pages and read embedded GeoPDF georeferencing in this build is **unverified** (spike S-1). |
| Scanned sheets as JPEG/PNG/JP2 | **Missing** | JPEG/PNG are accepted only as *ground photographs*; `overhead_raster` accepts only `.tif/.tiff`. The JP2 driver exists in GDAL but is not wired. |
| Shapefile and other vector formats | **Missing** | No vector modality, table or reader. `fiona`, `pyogrio`, `pyshp` and `osgeo` are **not installed**; rasterio does not read vectors. Adding any needs approval and a Smart App Control load test (see risks). |
| Display CRS / EPSG, datum, projection, units, dimensions, resolution, bounds, NoData, sidecars | **Partial** | Stored: `crs_identifier`, `crs_name`, `crs_wkt`, `pixel_units`, `width/height`, `pixel_size_x/y`, `bounds_native`, `footprint_4326`, `nodata`, bands. Sidecars are listed. **Not exposed:** datum name, ellipsoid, prime meridian, projection method and its parameters, per-axis units. These can be computed from the stored `crs_wkt` with pyproj at read time, so no stored change is needed. |
| Preserve the original spatial reference and transform correctly for display | **Supported** with a caveat | The original CRS is kept and tiles reproject from it. The caveat is the implicit operation choice (finding 4.4). |
| PDF / scan georeferencing workflow, no invented coordinates | **Partial** | Entering a geotransform by hand is supported and well validated. There is no control-point workflow and no residual reporting. |

### 4.2 ESM requirements (request section 5)

| Requirement | State | Evidence and gap |
|---|---|---|
| Support ESM sheets tied to the Everest datum | **Partial** | The registry holds Indian-subcontinent datums on Everest ellipsoids (section 6); none is named "Everest 1956". |
| Everest-56 datum, polyconic primary, LCC secondary | **Not established, by design** | The brief says to treat these as requirements to investigate. Section 6 records what the local registry does and does not contain. Nothing here assigns a CRS to ESM. |
| Do not assume all ESM sheets share one CRS | **Supported by design** | The current model is per raster. A custom-CRS registry and per-sheet declared metadata (proposed) keep it per sheet. |
| Verified CRS definitions and datum transformations | **Missing** | No custom CRS; no operation selection; no grids (section 6). |
| Warn and ask rather than silently approximate | **Partial** | `TRANSFORMATION_AVAILABLE` is `warning` when accuracy is unstated, but ingestion (`_footprint`) still places the raster and records `accuracy_m: null` without any flag, and the point converter returns a result with only an `ACCURACY_NOT_STATED` tag. A ballpark operation is not distinguished from a valid one. |

### 4.3 CRS handling (request section 6)

| Requirement | State | Notes |
|---|---|---|
| 1. Read CRS metadata from uploads | **Supported** | WKT from GeoTIFF; sidecar `.prj`. |
| 2. Identify unknown/incomplete definitions | **Partial** | Status `valid/missing/invalid` plus flags `MISSING_CRS`, `INVALID_CRS`, `CRS_NO_AUTHORITY_CODE`. "Ambiguous" (several registry matches) exists only inside the sidecar parser. |
| 3. Assigning versus transforming | **Supported** | Explicit in `georeference.py` and in the UI copy: assigning keeps the numbers; transforming changes them. Reproject is never applied to stored pixels. |
| 4. Display source and destination CRS | **Partial** | Source shown; the destination (EPSG:4326 for footprints, 3857 for tiles) is implicit. |
| 5. Use appropriate PROJ operations | **Partial** | PROJ default only (finding 4.4). |
| 6. Report unavailable grids/parameters | **Missing** | `TransformerGroup.unavailable_operations` is never consulted. |
| 7. Preserve source coordinates and metadata | **Supported** | Immutable file columns, immutable blobs. |
| 8. Validate transformed geographic bounds | **Supported** | `_footprint` rejects non-finite or out-of-range results; `WITHIN_AREA_OF_USE` warns. |
| 9. Record transformation provenance | **Partial** | `footprint_transformation` stores PROJ definition, accuracy, PROJ and pyproj versions. It does not store the operation's authority code, the candidate list, grids used or the reason it was chosen. |
| Systems: WGS84, UTM | **Supported** | Registry plus tests; round-trip verified (probe E). |
| Systems: Everest-56, Polyconic, LCC | **Not supported as such** | Registry facts in section 6. Polyconic and LCC *methods* exist in PROJ; a CRS using them on an Everest-family datum with explicit parameters must come from a verified source. |

### 4.4 Transformation: the central finding

All conversions use `Transformer.from_crs(...)` with PROJ's default operation selection, and tile rendering uses GDAL's
`reproject`, which also selects its own default. Places affected: `ingestion/raster.py::_footprint`, `geo/georeference.py`
(corners and footprint), `api/routers/layers.py::transform_point`, `api/routers/rastertools.py::get_pixel`, `geo/measure.py`,
`geo/tiles.py`. The recorded footprint accuracy can be `null` and nothing downstream treats `null` as a warning sign. For the
datums that matter here that is not acceptable (probes A to D, section 6).

### 4.5 Map and satellite overlay (request section 7)

| Requirement | State | Evidence |
|---|---|---|
| Load an OSM/ESM sheet | **Partial** | Only a georeferenced `.tif` sheet loads. |
| Load satellite GeoTIFF | **Supported** | Any valid `overhead_raster`; band selection and stretch (`display.py`); verified display copies for large rasters. |
| Toggle layers; change opacity | **Supported** | `LayerPanel` checkbox and slider; `raster-opacity` set on the MapLibre layer. |
| Control layer ordering | **Missing** | Layers are added in project order, all inserted below `aoi-fill`; there is no reorder control. |
| Zoom and pan | **Supported** | MapLibre. |
| Cursor coordinates | **Partial** | A live decimal-degree pointer readout (WGS 84) and a pinned-point converter to the CRS of any visible layer exist (`/crs/transform`). There is no live readout in the sheet's own CRS, and no datum label. |
| Compare sheet with imagery | **Missing** | No swipe, flicker or side-by-side mode. (The Phase 3b pixel-difference tool requires identical grids and is not an overlay aid.) |
| View raster metadata | **Partial** | Layer details panel and asset page; missing datum/projection parameters (4.1). |
| Identify alignment errors or missing georeferencing | **Partial** | Missing georeferencing is listed with reasons under "Not placed". Alignment error has no tool: no check points, no offsets, no footprint overlap figure. |
| Overlays must use real transformations | **Supported** | Tiles reproject from the CRS and affine; nothing aligns by size or appearance. |

### 4.6 User experience and statuses (request section 8)

The five-step flow (import, inspect, resolve CRS, georeference, display, overlay) exists in pieces: Upload and Ingestion,
Asset detail (metadata, Georeferencing, Sidecars), Map & Analysis. It is not presented as one workflow and the requested
statuses are not shown together. Mapping of each status to what can be derived today:

| Requested status | Derivable today from | Gap |
|---|---|---|
| Georeferenced | `Effective.georeferenced` | none |
| CRS identified | `crs_status = valid` and a registry code or WKT | none |
| CRS missing | `crs_status = missing` | none |
| CRS ambiguous | sidecar parser: several full matches, or a WKT with no complete match | not available for the file's own CRS or for display |
| Transformation available | PROJ can build a pipeline | needs the classification (exact, approximate, ballpark, ambiguous) |
| Transformation unavailable | `TransformerGroup.unavailable_operations` (grid missing) or no operation | not computed today |
| Georeferencing required | `unplaced_reason()` | present, free text only |
| Validation failed | `GEOREFERENCE_INVALID` and `INVALID_GEOREFERENCING` | present for recorded versions and ingestion |

### 4.7 Data model and migrations

- Migration chain `0001` to `0009`; the working database is at `0009`. *(Stale: `0010` is now the unapplied
  `derived_index_job_kind` migration for NDVI Studio. The audit-protection and role work (gate G6) is still not approved and has no
  number. Any new migration must be sequenced after both.)*
- `asset.modality` has `CHECK (modality IN ('ground_image','overhead_raster','video'))` (migration 0002). A new modality
  needs a migration, as do the constraints on `georef_assertion.operation`, `.method`, `.source`, `asset.origin`, and `job.kind`
  (for a "render PDF" job).
- `crs_definition` already exists (columns `identifier`, `kind`, `wkt2`, `units`, `axis_order`, `area_of_use`,
  `source_reference`, `approved_by`, `status` default `unclaimed`) and is used by nothing but a test cleanup list. It has no
  versioning, hash or append-only trigger.
- `asset_lineage` (append-only) already models "child derived from parent" and fits a PDF rendering.

### 4.8 Tests and documentation

Strengths: georeferencing, sidecar, layer, tile, CRS and terrain behaviours are covered with synthetic in-memory GeoTIFFs
(`tests/media.py::geotiff_bytes`, EPSG:32643 only). Browser specs exist for map, georeferencing and sidecars.
Gaps: no fixture with a non-WGS84/non-UTM datum, no custom CRS, no ballpark or ambiguity case, no GCP, no PDF, no
tests for a missing transformation grid, and no overlay-alignment test between two different CRSs.

Documentation: `docs/architecture/decision-log.md` leaves **U-006** (authoritative CRS list and grids) and **U-013** (offline
basemap) open and records **U-007** (a Defence Series Map CRS definition) as "not claimed". The Settings screen shows a
"DSM compatibility is not claimed" notice. With DSM now out of scope, those texts should be revisited (Phase 8 in section 13, question 11 in section 15).

## 5. Reusable existing components

| Component | Reuse for |
|---|---|
| `geo/crs.py` (offline registry search, strict identifier rule) | Basis of the new CRS resolver; keep the rule that names are never CRSs. |
| `geo/georeference.py` (`validate`, `apply`, `Effective`, versioning, optimistic concurrency, checksum guard) | The recording engine for every georeferencing method, including GCP fits. |
| `geo/sidecars.py` and its router/panel | World files, `.prj`, `.aux.xml`; add shapefile `.prj`/`.cpg` handling later. |
| `geo/tiles.py` + `/assets/{id}/tiles` + display copies | All sheet and imagery rendering. Needs an optional explicit-operation parameter (spike S-2). |
| `RasterMetadata` immutability trigger + `GeorefAssertion` append-only table | Provenance model; extend, do not replace. |
| `asset_lineage`, `Artifact`, job queue and worker | PDF rendering as a derived raster; statistics and display copies already run this way. |
| `MapCanvas` / `MapPage` `LayerPanel` | Layer toggles, opacity, footprints, pointer readout. Add ordering, compare and CRS readout. |
| `GeoreferencingPanel` (propose, review checks, confirm, history) | Host for the GCP method and the status badges. |
| `CrsPicker` and `/api/v1/crs` | Extend with custom (registered) CRSs, clearly labelled as such. |
| `rastertools` pixel and measure endpoints | Cursor and check-point coordinate lookups. |
| `ingestion/rules.py` and the streaming uploader | New extensions and signatures (`%PDF`, JP2). |
| Report and export modules (`reporting.py`, observation exports) | Later: CRS and transformation provenance in reports. |
| Synthetic-media test helpers | Extend with new fixture builders. |
| Terrain/3D (`terrain.py`, `ThreeDPage`) | Unchanged; it keeps working through `georeference.effective`. |

## 6. Coordinate-system findings from the local PROJ database

Read-only probes against the installed `proj.db` (PROJ 9.5.1; authorities EPSG, ESRI, IAU_2015, IGNF, NKG, NRCAN, OGC, PROJ).
Network is disabled and **no grid files are installed** (the PROJ data folder holds only `CH`, `GL27`, ITRF files and
`nad27/nad83`-style lists; there are no `.tif`, `.gsb` or `.gtx` grids).

**Datums and ellipsoids that exist**

- Ellipsoids: `Everest 1830 (1937 Adjustment)` (EPSG:7015), `(1967 Definition)` (7016), `Modified` (7018), `(1830 Definition)`
  (7042), `(1962 Definition)` (7044, a = 6377301.243), `(1975 Definition)` (7045), `(RSO 1969)` (7056).
- Geodetic datums: Indian 1954 (EPSG:6239), Indian 1960 (6131), Indian 1975 (6240), Kalianpur 1880 (6243), Kalianpur 1937 (6144),
  Kalianpur 1962 (6145), Kalianpur 1975 (6146); ESRI `D_Everest_India_Nepal` (106203, on ellipsoid 7044) and `D_Everest_Bangladesh`.
- **No datum, CRS or ellipsoid is named "Everest 1956" or "Everest-56" in the EPSG or ESRI registry here.** Whether the
  client's "Everest-56" corresponds to one of the datums above is not something this assessment can decide. The numeric
  coincidence of ellipsoid 7044's semi-major axis with the figure often quoted for Everest 1956 is a lead to verify against
  authoritative documentation, **not** a basis for assigning anything.

**Projections that exist**

- PROJ methods: `American Polyconic` (EPSG:9818), `Lambert Conic Conformal (1SP)` (9801), `(2SP)` (9802), (2SP Belgium), (West
  Orientated), (1SP variant B), (2SP Michigan).
- Registered projected CRSs on Everest-family datums are **UTM zones** (for example Kalianpur 1962 / UTM zone 43N,
  EPSG:24313, Transverse Mercator) and the **India zone 0, I, IIa, IIb, IIIa, IVa** Lambert Conic Conformal (1SP) CRSs
  (EPSG:24370 to 24383). Newer WGS 84 LCC CRSs exist (EPSG:7755 India NSF LCC, 7771 India Northeast).
- **No registered CRS combines a polyconic projection with an Everest-family datum.** The nine registered polyconic CRSs are
  Brazil, Panama, Guam and ESRI world/sphere ones. A polyconic ESM sheet therefore cannot be expressed by a registry code; it
  needs a custom definition with explicit parameters and a recorded source.

**Datum transformation behaviour (probes, nothing stored)**

| Probe | Result | Consequence |
|---|---|---|
| A. Custom `+proj=poly` on an Everest ellipsoid with no datum shift | PROJ builds `... + Ballpark geographic offset from unknown to WGS 84 ...`, **accuracy -1 (unknown)** | Silent approximation. The current code would place such a raster with `accuracy_m: null`. |
| B. Kalianpur 1962 to WGS 84 | 7 operations: accuracies 999 m, 1 m, 3 m (four), plus a ballpark | Ambiguous: which is correct depends on the sheet and region. |
| C. Indian 1975 to WGS 84 | 3 operations: 3 m, 5 m, 1 m | Ambiguous. |
| D. Kalianpur 1962 / UTM 43N to WGS 84 / UTM 43N | PROJ's default is "Kalianpur 1962 to WGS 84 (1)" (stated accuracy 999 m); a point at (500000, 3000000) moved about **279 m** | The default choice is not a safe silent default. |
| E. UTM 43N to WGS 84 and back | Exact round trip to 0.0001 m, no datum shift | WGS84/UTM conversion is unambiguous. |
| Grids | Zero operations were "unavailable because a grid is missing" for these datums | The registered shifts here are Helmert-type; if an authoritative source requires a national grid, it must be supplied, approved and installed (U-006). |

These values are probe facts about the installed database, not statements about any real ESM sheet.

## 7. Proposed architecture

Principles (all inherited from the current design, plus three new ones):

1. A CRS is identified by a registry code or by a **complete, sourced, approved custom definition**; never by a name, a series
   label or a filename.
2. Assigning a CRS, recording georeferencing, and transforming coordinates stay three separate, labelled actions.
3. The original file and its declared metadata never change; everything a person adds is an append-only version with a reason,
   actor, checksum and validation.
4. *(new)* A transformation is always an **explicit, recorded operation**: authority code or pipeline, stated accuracy,
   grids used, why it was selected, and by whom if a person chose it. A ballpark operation is never presented as valid.
5. *(new)* "Series" (OSM/ESM) is a **label that a person declares**, never an input to CRS resolution.
6. *(new)* Alignment between a sheet and imagery is **measured at check points a person identifies**, never inferred from
   appearance or by automatic image matching.

```
                    ┌──────────────────────────── CRS assessment (new, read-only, computed) ────────────────────────────┐
 map_sheet label ─► │ file CRS ─┐                                                                                        │
 (declared, versioned)│ sidecars ├─► resolver ─► {datum, ellipsoid, projection + parameters, units, status, candidates} │
 custom CRS registry ─┘ recorded │                                                                                        │
 (sourced, approved)    georef ──┘                                                                                        │
                    └───────────────┬────────────────────────────────────────────────────────────────────────────────────┘
                                    ▼
                   Transformation planner (new): TransformerGroup, network off
                   classify each candidate: exact | approximate(accuracy) | ballpark | unavailable(grid) | ambiguous
                   selection recorded (project-level choice or per georeference version) ──► provenance
                                    ▼
   Georeferencing engine (existing georeference.validate/apply) with methods:
   assign_crs · set_geotransform · world file · .prj (existing)  +  fit_gcps (new)
                                    ▼
   Tiles (existing) with explicit operation  ──►  Map & Analysis: ordered layers, opacity, compare, CRS readout, check points
```

### 7.1 Map sheet as an asset

- New modality `map_sheet` (a raster page of a printed or published map). It reuses `raster_metadata`, sidecars, georeferencing
  and tiles; it differs in intent, accepted formats (`.tif/.tiff`, and after the spike `.pdf`, JPEG/PNG scans, JP2) and a small
  declared-metadata record: series (`OSM`, `ESM`, `other`, `unknown`), sheet reference, edition, scale denominator, and the
  datum, projection and grid information the sheet *states in its margin* as **claims with a source**, not as CRS.
- Satellite imagery stays `overhead_raster`. 3D terrain stays as it is.

### 7.2 CRS assessment

A pure function over data already stored (file metadata, sidecars, latest recorded version, custom registry, declared sheet
claims), computed on read, returning: the CRS in use and its origin; datum, ellipsoid, prime meridian; projection method and
parameters; axis names, directions and units; area of use; the requested statuses; the reasons in plain language; and the
transformation plan to the display CRS. It adds a **consistency check between declared claims and the file** (for example a
margin note that says "Polyconic" while the file says Transverse Mercator becomes `CRS_CLAIM_CONFLICT`, shown, not resolved
by guessing).

### 7.3 Custom CRS registry

Use the reserved `crs_definition` table, made versioned and append-only. A custom CRS needs: a complete WKT2 or PROJJSON
definition that PROJ parses to a 2D geographic or projected CRS; a recorded source (document, page, edition); the entering
profile; a content hash; and an approval step. Free-text names, datum-only entries and "WGS84 + LCC" remain refused. Every
use of a custom CRS in a georeferencing version is labelled `user_asserted`, as today. Profiles are attribution, not
authentication (decision U-009 is open), so "approved" means "a named profile approved it", and the UI must say so.

### 7.4 Transformation planning

A new module (`geo/transform.py`) wraps pyproj `TransformerGroup` with the network disabled and returns an ordered candidate
list with: operation name and authority code, pipeline, stated accuracy, area of use, grids required and whether they are
present, and a class. Policy:

- **exact**: no datum change, or a grid-based operation with the grid present. Used without confirmation.
- **approximate**: a registered Helmert-type operation with stated accuracy. Allowed only after a person selects it; the
  selection and accuracy are recorded and shown wherever the result is used.
- **ballpark** (accuracy unknown) or **grid missing**: *not used*. Status `transformation_unavailable` with the missing
  information stated and what would resolve it.
- **ambiguous** (several approximate operations apply): status `crs_ambiguous` or `transformation_ambiguous`; a person must
  choose, with the candidate list and accuracies in front of them.

The footprint recorded at ingestion keeps working for the unambiguous cases and, for the others, sets an explicit
`TRANSFORMATION_*` flag instead of a silent `accuracy_m: null`.

### 7.5 Georeferencing a scan: ground control points

A new method `fit_gcps` on the existing engine. A person enters control points (pixel column/row on the sheet, with the sheet
coordinates read from the printed grid or graticule, in a chosen and recorded CRS). The engine fits an affine transform by
least squares (three or more points; more points give residuals) and reports per-point residuals and RMSE in pixels and in
ground units, rejecting a fit that exceeds a stated tolerance or that has collinear points. The result is stored as an ordinary
geotransform in a new version (so tiles, footprints, measurements and 3D keep working unchanged), with the control points and
fit statistics in `validation`. Polynomial and thin-plate-spline models, which cannot be stored as one affine, are a later
extension (they need a stored model and a GCP-aware tile path). Paper distortion in scans can exceed an affine's ability to
fit, which the residual report makes visible rather than hiding.

### 7.6 Overlay and alignment checks

- Layer order: a persisted per-project order (view state) applied to MapLibre layers; no data change to the rasters.
- Compare: swipe and flicker of two chosen layers; both rendered from real tiles.
- Readout: pointer coordinates in WGS 84 plus the active sheet's own CRS (with datum label) using the same recorded operation as
  the tiles.
- Check points: a person marks the same feature on the sheet and on the imagery; the tool stores the pair and reports the
  offset (metres and pixels), per-point and summarised. These are append-only records. No automatic image matching, no
  automatic shifting. The sheet is never warped to fit the imagery.
- Footprint overlap (intersection over union in EPSG:4326) as a coarse sanity check.

### 7.7 Where code would live

Backend: `geo/assessment.py`, `geo/transform.py`, `geo/gcp.py`, `geo/customcrs.py`, `ingestion/pdf.py` (after spike),
`api/routers/crs.py` (extend), `api/routers/mapsheets.py`, `api/routers/alignment.py`; extend `layers.py`, `georeference.py`,
`ingestion/rules.py`, `ingestion/raster.py`. Frontend: `components/CrsAssessmentPanel.tsx`, `ControlPointEditor.tsx`,
`LayerOrderList.tsx`, `ComparePane.tsx`, `AlignmentChecks.tsx`; extend `MapPage.tsx`, `MapCanvas.tsx`,
`GeoreferencingPanel.tsx`, `IngestionPage.tsx`, `CrsPicker.tsx`.

## 8. Missing capabilities (consolidated)

1. Map-sheet modality and its declared metadata.
2. Upload of PDF, JPEG/PNG/JP2 scans; PDF page rendering as a derived, lineage-tracked raster.
3. Vector map import (shapefile and similar) and a vector storage and display path.
4. A combined CRS assessment with datum, ellipsoid, projection parameters, units and the requested statuses.
5. A custom CRS registry with sourcing and approval.
6. Explicit, classified, recorded datum transformations; detection of ballpark, ambiguous and grid-missing cases.
7. GCP-based georeferencing with residuals.
8. Layer ordering, compare mode, sheet-CRS coordinate readout.
9. Check-point alignment measurement.
10. CRS and transformation provenance in reports and exports.
11. Fixtures and tests for non-UTM datums, custom CRS, ballpark and ambiguity, GCP, PDF, overlay alignment.
12. A single guided "import, inspect, resolve, georeference, display, overlay" flow with the eight statuses.

## 9. Proposed API changes (additive; none breaks a current client)

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/assets/{id}/crs-assessment` | Read-only assessment object (7.2): CRS in use and origin, datum, ellipsoid, projection and parameters, units, area, statuses with reasons, claim conflicts, transformation plan. |
| `GET /api/v1/crs/operations?source=&target=&west=&south=&east=&north=` | Candidate operations with class, accuracy, grids, area of use. |
| `GET /api/v1/crs/transform` (extend) | Optional `operation` parameter; response adds `operation` (code or pipeline), `class`, `accuracy_m`, `grids`; refuses ballpark and ambiguous cases with a problem document that lists the candidates. Old calls without `operation` keep working only when the plan is unambiguous. |
| `POST /api/v1/crs/definitions/validate`, `POST/GET /api/v1/crs/definitions`, `POST /api/v1/crs/definitions/{id}/approve` | Custom CRS registry (7.3). Validation is stateless. |
| `POST /api/v1/projects/{id}/assets` with `modality=map_sheet` | Upload; same streaming and checks, new accepted extensions and signatures. |
| `GET/POST /api/v1/assets/{id}/map-sheet` | Declared sheet metadata and claims, versioned. |
| `POST /api/v1/assets/{id}/render-pdf` (job) | Render a chosen PDF page at a stated DPI into a derived raster; lineage `rendered_from_pdf`. |
| `POST /api/v1/assets/{id}/georeferencing[/validate]` (extend) | New `operation: "fit_gcps"` with `control_points`, `crs_id`, `fit: "affine"`; the validate call returns residuals and the checks. |
| `GET /api/v1/projects/{id}/layers` (extend) | Per layer: `kind` (`map_sheet`/`imagery`), `assessment_status`, `transformation` (operation, class, accuracy), `z_order`. |
| `GET/PUT /api/v1/projects/{id}/layer-order` | Persisted view order only. |
| `POST/GET /api/v1/projects/{id}/alignment-checks` | Append-only check-point pairs and the derived offsets. |
| `GET /api/v1/projects/{id}/reports/findings` (extend, later) | CRS and transformation provenance section; bump `schema_version` additively as was done for annotations. |

All new request bodies follow the existing conventions: Problem JSON errors, optimistic version checks, an audit entry for
every accepted or refused change, and OpenAPI plus generated TypeScript types regenerated.

## 10. Proposed database changes (none applied; each needs its own approval)

| Change | Why |
|---|---|
| `asset.modality` CHECK adds `map_sheet` | New modality. |
| New table `map_sheet_info` (append-only versions): `asset_id`, `version_no`, `series`, `sheet_ref`, `edition`, `scale_denominator`, `claimed_datum`, `claimed_projection`, `claimed_grid`, `claims_source`, actor, request id | Declared metadata and claims. |
| `crs_definition` made versioned and append-only: `version_no`, `projjson`, `sha256`, `source_type`, `source_reference` (required), `status` (`draft/approved/retired`), entered and approved by, area of use | Custom CRS registry. Needs the immutability trigger the other history tables have. |
| `georef_assertion`: allow `operation = 'fit_gcps'`, new methods/sources; new columns `control_points JSONB`, `fit JSONB`, `transformation JSONB` (selected operation) | GCP method and transformation provenance. |
| New table `transformation_choice` (append-only): project, source CRS, target CRS, operation, class, accuracy, reason, actor | Project-level approved operation that tiles, readouts and exports share. |
| New table `alignment_check` (append-only): project, sheet asset, imagery asset, sheet pixel, imagery location, offsets, actor | Check-point records. |
| `asset.origin` / `job.kind` / `asset_lineage.relation` CHECKs add the PDF render values | PDF derivation. |
| Optional: store the assessment snapshot on each georeference version | Reproducibility of what was shown. |

Migration notes: *(stale numbering, see the status update: `0010` is taken by `derived_index_job_kind`)*; sequence after the G6 decision; every new table follows the `0008`/`0009` pattern (CHECKs, an
append-only trigger, a downgrade that refuses when rows exist); a rehearsal on a copy of the database and a verified backup
come first, exactly as for `0009`.

## 11. Frontend changes

- **Upload and Ingestion**: a "Map sheet" input type; accepted-format list from `/ingestion/rules`; sidecar attach for scans;
  clear text that the series label does not set the CRS.
- **Asset / sheet page**: a "Map data workflow" stepper (Import, Inspect, Resolve CRS, Georeference, Display, Overlay) with the
  eight status badges and plain-language reasons; a metadata panel with datum, ellipsoid, projection and parameters, units,
  bounds, resolution, NoData and associated sidecars; the declared-claims editor and any conflict.
- **CRS resolution**: a candidate list with registry matches, the custom-CRS form (WKT2/PROJJSON, source, approval), and the
  transformation chooser showing each candidate's accuracy and class.
- **Georeferencing panel**: control-point table and click-to-add on the image, residual table and RMSE, accept or reject.
- **Map & Analysis**: draggable layer order; sheet and imagery badges (`Georeferenced`, `User-asserted`, `Approximate: 3 m`);
  swipe and flicker compare; pointer readout in the sheet CRS with datum; check-point tool and offset summary; clearer
  "Not placed" list that links to the right fix.
- **Settings**: replace the DSM notice with a scope statement (subject to question 11 in section 15); show the installed PROJ version, authorities
  and "no transformation grids installed".
- Accessibility and the existing test conventions (roles, labels, `data-testid` only where needed) are kept.

## 12. Test plan

Synthetic fixtures only. No licensed sample sheet is available, so **nothing below claims validation of a real OSM or ESM
sheet**. Real-sample validation is a separate acceptance step once samples are provided.

New fixture builders in `tests/media.py`: GeoTIFF in a chosen CRS (EPSG codes, custom WKT, PROJ strings), with and without CRS,
with a degenerate or identity geotransform, with GCPs, a TIFF plus world file and `.prj`, a scanned-looking RGB raster, a
small generated PDF (if the spike succeeds), a two-raster pair with a known shift.

| Requirement (request section 9) | Tests |
|---|---|
| OSM GeoTIFF ingestion | Ingest in EPSG:4326, a UTM zone, and a registered Kalianpur UTM; assert datum, projection, units, bounds, NoData, resolution, footprint and the `CRS identified`/`Georeferenced` statuses. |
| TIFF and world file | Existing sidecar tests plus: rotated world file, world file without CRS (`georeferencing_required`), world file with a mismatching `.prj`. |
| Missing CRS | Status `CRS missing`, not placed, tile refused, a CRS from a *filename or series label* never applied (explicit negative test). |
| Incorrect or incomplete CRS metadata | WKT that names UTM 43N with wrong parameters; datum-only WKT; "WGS84 + LCC" refusal at every entry point; ambiguous registry match gives `CRS ambiguous`. |
| Everest-datum transformation with a verified definition | Using a *registered* Everest-family CRS (for example EPSG:24313 to EPSG:32643 with an explicitly selected operation): assert the recorded operation, its stated accuracy, and that the result equals pyproj's output for that operation. Using a **custom** definition: only with a synthetic definition whose source is marked "test fixture"; the test asserts the machinery, not the truth of the parameters. |
| Ambiguity and missing information | Kalianpur/Indian datums return several candidates and require a choice; the default pick is never taken silently; a custom CRS without datum shift is `transformation_unavailable`, never ballpark. |
| UTM and WGS84 conversion | Round trip within a stated tolerance, axis order (x first), hemisphere and zone edges. |
| Polyconic and LCC | Registered LCC (EPSG:24371) forward and inverse against pyproj; custom polyconic and LCC definitions for forward/inverse consistency (round trip) and parameter completeness checks. |
| Raster bounds validation | Coordinates outside the CRS domain; footprint crossing the antimeridian or poles; bounds in the wrong CRS. |
| Satellite overlay alignment | Two synthetic rasters in different CRSs with a known ground feature: tiles of both agree on that feature within a stated pixel tolerance at several zooms; check-point offsets match the injected shift; footprint overlap figure. |
| Missing transformation grids | Simulated by a candidate list with `grid missing` (monkeypatched planner); status `transformation_unavailable`, no tile rendered, message names the grid. |
| GCP georeferencing | Exact points give zero residual; noisy points give the expected RMSE; collinear or too few points refused; fit stored as a normal version; original unchanged. |
| Existing raster regression | The current suites for ingestion, rasters, tiles, georeferencing, sidecars, raster tools and terrain must pass unchanged. |
| Photo, video, observation, reporting regression | Existing suites run unchanged (`check.ps1`); the findings-report contract tests and OpenAPI drift/contract tests updated additively. |
| Frontend | Component tests for statuses, assessment panel, control-point editor, layer order, compare, readout; browser specs for the end-to-end flow on a synthetic sheet. |
| Safety | Tests that no code path writes `raster_metadata` file columns, that `crs_definition` rows cannot be edited, and that no network request is made. |

## 13. Phased implementation plan

Each phase ends with the full test suite, a rehearsal on a scratch database where a migration is involved, and the same
separate approval pattern used for G1 to G5. No phase starts without that approval.

| Phase | Scope | Migration? | Exit criteria |
|---|---|---|---|
| **0. Samples and decisions** (no code) | Obtain licensed, permitted sample sheets and their authoritative CRS documentation; decide the open questions (section 15). | none | Written answers; sample list with licences. |
| **1. CRS assessment and transformation transparency** *(recommended next)* | `geo/assessment.py`, `geo/transform.py`, read-only `crs-assessment` and `crs/operations` endpoints; classify operations; surface the class and accuracy in footprint flags, `/crs/transform`, layers and the UI metadata panel; the eight statuses; datum/projection/parameters display; fixtures and tests. | **none** | Ballpark and ambiguous cases are flagged everywhere they are used; all existing tests pass; new tests pass; no stored data changes. |
| **2. Map-sheet import and metadata** | `map_sheet` modality; `map_sheet_info`; scan formats (TIFF first; JPEG/PNG/JP2 after a signature review); guided workflow UI. | yes (rehearse) | Sheet uploaded, inspected and listed with statuses; TIFF + world file path works end to end. |
| **3. Custom CRS registry and explicit transformation choice** | `crs_definition` versions and approval; `transformation_choice`; spike S-2 then explicit operation in tile rendering and readouts. | yes (rehearse) | A sourced custom CRS can be defined, approved and used; tiles, footprints and readouts use the recorded operation. |
| **4. GCP georeferencing (affine)** | `fit_gcps`, residual report, control-point editor. | yes (rehearse) | A synthetic scan is georeferenced within tolerance; refusals work; history shows the points. |
| **5. Overlay workspace** | Layer order, compare modes, sheet-CRS readout, check points and alignment report. | yes (small) | Known synthetic shift is measured correctly; no automatic alignment exists. |
| **6. PDF map sheets** | Spike S-1, then `render-pdf` job, lineage, page and DPI choice, embedded georeference read if present. | yes (small) | A generated PDF becomes a derived raster; its georeferencing still requires phases 3 to 4. |
| **7. Vector maps** | Decision on reader (see S-3), storage and display, shapefile bundle upload with `.prj` through the CRS assessment. | yes | Shapefile geometry placed only with a resolved CRS. |
| **8. Hardening** | Provenance in reports and exports, performance on large sheets, accessibility, runbook and acceptance checklist, update decision log (U-006, U-007, U-013). | maybe | Acceptance checklist extended; full `check.ps1` green. |

**Spikes (small, throw-away, before the phase that depends on them; each needs approval if it installs anything):**

- **S-1** Can the installed GDAL PDF driver open a generated PDF, choose a page and DPI, and read embedded georeferencing? No
  PDF library is installed.
- **S-2** Can GDAL warp use an explicit coordinate operation through rasterio 1.4.3 (the `COORDINATE_OPERATION` transformer
  option), so that tiles use the recorded operation rather than GDAL's default? If not, the fallback is to build the source
  window and a fixed control-point grid with pyproj and warp with an explicit pipeline.
- **S-3** Which vector reader, if any, loads under Windows Smart App Control (`pyogrio`, `fiona`, or pure-Python `pyshp`).

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Silent ballpark or low-accuracy datum shifts (probe A, D) | A sheet placed hundreds of metres off with no warning | Phase 1 classification; refuse or require choice; show accuracy wherever coordinates are used. |
| GDAL's tile reprojection chooses its own operation | Tiles disagree with readouts | Spike S-2; until then show the class and accuracy on the layer and block ballpark. |
| No authoritative definition of Everest-56, polyconic or LCC parameters | Wrong custom CRS | Custom CRS requires a source and approval; label `user_asserted`; real-sample validation is a separate step. |
| Profiles are not authentication | "Approved" CRS is only attribution | State it in the UI; role model U-009 stays open. |
| Scan distortion (paper shrinkage, folds) | Affine fit leaves large residuals | Report residuals; refuse above tolerance; plan polynomial/TPS later. |
| Registry `.prj` matching is exact | ESRI WKT1 `.prj` files often match no entry | Route to "no complete match" and the custom CRS path; never pick the nearest. |
| No offline datum grids | Higher-accuracy national shifts unavailable | Grid supply and licence are decision U-006; report `grid missing`. |
| New native dependencies blocked by Windows Smart App Control | Vector/PDF features cannot load | Spike S-3; pin and load-test as the project already does. |
| Large scans (limits: 1 GB upload, 400 M pixels compare) | Memory and time | Reuse display copies and windowed reads; stream; keep existing caps; add timing tests. |
| Licence and classification of real sheets | Legal and policy exposure | Confirm the sheets are permitted for this use before importing; DSM and restricted workflows explicitly excluded. |
| Existing DSM wording | Contradicts the new scope | Revise Settings text, `U-007` and the traceability rows in Phase 8, with approval. |
| Migration numbering and G6 | Collision with the pending `0010` *(now taken by `derived_index_job_kind`, unapplied)* | Sequence after the G6 decision and after `0010`. |
| Scope growth | Phases 6 and 7 are open-ended | Gate each phase; the recommended slice (Phase 1) is self-contained. |
| Regression in photo, video, observation, reporting, 3D | Existing users affected | Additive APIs only; full existing suites in every phase; 3D reads the same `effective()` state. |

## 15. Unresolved questions

1. What exactly do OSM and ESM sheets look like as delivered: georeferenced GeoTIFF, scanned TIFF with world file, PDF? Which
   do the clients actually have?
2. What is the authoritative definition of "Everest-56" for the target sheets (ellipsoid, datum shift source, region), and which
   sheets use polyconic versus LCC? Is the choice per sheet, per series or per region?
3. What are the polyconic parameters (origin, central meridian, false easting/northing) and LCC parameters (standard
   parallels, origin), and where are they printed or documented?
4. Which datum-shift source is acceptable, and are national grids available and licensed for offline use? (U-006)
5. May real sample sheets be provided for validation, and under what licence and handling rules? Are OSM and ESM sheets
   publicly redistributable in this project?
6. What accuracy tolerance applies to georeferencing residuals and to sheet-versus-imagery alignment?
7. Who may approve a custom CRS, given there is no authentication? Is "named profile approved" enough for the PoC?
8. Should the sheet or the imagery be the default base layer, and should the application ever offer an offline basemap (U-013)?
9. Is exporting a transformed or georeferenced sheet (for example a derived GeoTIFF) required, or only display and
   measurement?
10. Which vector formats beyond shapefile matter, and is vector storage in the database acceptable?
11. May the existing "DSM compatibility is not claimed" notice and decision U-007 be replaced by a scope statement?
12. Should migration numbering for this work wait for the G6 decision?

## 16. Report: what was inspected, what is supported, what is missing, what next

**Inspected:** the files and sources listed in section 2, plus read-only probes of the installed PROJ database and library
versions. No source, database, setting, service or test run was touched.

**Already supported:** local-only georeferenced GeoTIFF ingestion with CRS, bounds, resolution and NoData; the registry
CRS search and strict "no incomplete CRS" rule; world-file, `.prj` and `.aux.xml` review and confirmation; georeferencing by
CRS assignment or entered geotransform with validation, versioning, audit and tile cache invalidation; reprojected tiles with
rotation support; layer visibility and opacity; pointer coordinates; pixel lookup and measurement; derived rasters with
lineage; 3D terrain; reports and exports; the append-only provenance pattern.

**Missing:** the map-sheet concept and non-TIFF inputs (PDF, scans, vector); control-point georeferencing; custom CRS
definitions; explicit, classified, recorded datum transformations (the current defaults can be silent ballpark or a 279 m
different choice for Kalianpur/Indian datums); layer ordering, compare and sheet-CRS readout; alignment check points; a
single status model and guided workflow; targeted tests and fixtures; provenance in reports.

**Recommended next phase:** Phase 1, CRS assessment and transformation transparency: read-only, no migration, no stored data
changed, additive API and UI, and the safest way to remove the one risk (silent approximate placement) before any map-sheet
feature is built on top of it. Phase 0 (samples and decisions, section 15) should run in parallel.
