# CRS conversion coverage: every place coordinates change reference system

OSM/ESM work, Phase 3. The Phase 1 transformation planner (`geopoc.geo.transform`) is now the single authority for every
conversion that moves positions between reference systems. This page is the audit that came first (the **Before** column) and
the result (the **After** column). It adds no database migration and changes no stored data.

## How a conversion is classified

| Mechanism | Meaning |
|---|---|
| **Planner** | The planner chose the operation: exact, the single registered shift, or the person's choice. Ballpark, missing-grid and unchosen-ambiguous cases are refused. |
| **PROJ default** | `Transformer.from_crs(...)` with PROJ choosing silently: it could use a ballpark, the first of several shifts, or no datum shift at all. |
| **Explicit PROJ** | A named PROJ operation that cannot change datum (an inverse projection to the CRS's own geographic CRS; WGS 84 to Web Mercator). Safe by construction. |
| **Custom** | Arithmetic written in the application (degree/metre size estimates, tile indexing). Not a datum operation. |
| **None** | The path never converts coordinates between reference systems. |

## Coverage matrix

| # | Path | Where | Before | After |
|---|---|---|---|---|
| 1 | Raster footprint at upload and for file-georeferenced rasters | `ingestion/raster.py footprint_checked` | Planner | Planner |
| 2 | Georeferencing validation outline | `geo/georeference.validate` | Planner | Planner |
| 3 | Georeferencing validation **corner readout** | `geo/georeference.validate` | **PROJ default** | **Planner** (the operation that placed the outline) |
| 4 | Georeferencing validation render check | `geo/georeference.validate` | Planner | Planner |
| 5 | Map layer outline and layer list | `routers/layers.py` | Planner | Planner |
| 6 | Tile generation and source window | `geo/tiles.py` | Planner (pipeline to GDAL); exact leaves GDAL's own identical no-shift default | Same |
| 7 | Point conversion `/crs/transform` | `routers/layers.py` | Planner | Planner, now through the shared converter |
| 8 | CRS operations list / CRS assessment | `routers/crs.py`, `geo/assessment.py` | Planner | Planner |
| 9 | **Pixel to longitude/latitude** | `routers/rastertools.py /pixel` | **PROJ default** | **Planner**; absent with a reason when not allowed |
| 10 | **Longitude/latitude to pixel** | `routers/rastertools.py /pixel` | **PROJ default** | **Planner**; refused (409) when not allowed |
| 11 | Distance/area, WGS 84 points from the map | `geo/measure.measure_lonlat` | None (geodesic on WGS 84) | None |
| 12 | Distance/area, pixel points: to the raster's own geographic CRS | `geo/measure.measure_pixels` | Explicit PROJ (same datum) | Explicit PROJ (same datum), documented |
| 13 | **Measurement points shown as longitude/latitude** | `geo/measure.measure_pixels` | **PROJ default** | **Planner**; omitted with a reason when not allowed. The measured value never needed it |
| 14 | Photo location | `geo/photolocation.py` | None (WGS 84 only; anything else refused) | None |
| 15 | Map annotations | `geo/annotations.py` | None (WGS 84 only; anything else refused) | None |
| 16 | Map-sheet alignment | `geo/alignment.py` | Planner | Planner |
| 17 | Control-point georeferencing | `geo/controlpoints.py` + validation | Planner (through validation) | Planner |
| 18 | **Project area of interest to WGS 84** | `geo/aoi.py` | **PROJ default** | **Planner**; choice accepted with the AOI; provenance kept |
| 19 | **3D terrain longitude/latitude extent and centre** | `geo/terrain.build_grid` | **PROJ default** (projected); **assumed WGS 84 unchanged** (geographic) | **Planner**; absent with a reason when not allowed |
| 20 | 3D terrain metres per cell | `geo/terrain.build_grid` | Custom (geodesic on WGS 84 for degrees; unit factor for projected) | Unchanged: a size estimate, not a position. Elevation values and vertical datum untouched |
| 21 | Export and report coordinates | `reporting.py`, `annotation_export.py`, observation export | None (they read stored WGS 84) | None |
| 22 | Comparison alignment check | `geo/compare.py` | None (requires identical CRS; nothing is reprojected) | None |
| 23 | Satellite export (offline mock, Earth Engine disabled) | `satellite/mock.py` | **PROJ default** (WGS 84 to its WGS 84 UTM zone) | **Planner** (exact) |
| 24 | Tile indexing from WGS 84 | `geo/georeference._tile_of` | Explicit PROJ (WGS 84 to Web Mercator, same datum) | Same |
| 25 | Which regional operations apply to a place | `geo/transform.locate`, `ingestion/raster.py`, the area-of-use warning in `/crs/transform` | PROJ default, as a locator only | Same: never a result, only a box that selects which registered operations are listed |
| 26 | Degree/metre size for zoom choice and ground pixel | `layers.py`, `georeference._metres`, `alignment._ground_pixel` | Custom | Custom (labelled approximate) |
| 27 | Browser: Web Mercator and WGS 84 | MapLibre | Library | Library (display of WGS 84 positions only) |
| 28 | Browser: coordinate formatting (decimal degrees, DMS) | `lib/geo.ts` | Custom (formatting only) | Same |

## Accuracy gate (CRS correctness hardening, Phase 2)

Every path in the matrix above goes through `transform.resolve()`, which now also applies the accuracy gate: a datum shift worse
than 10 m, or with no stated accuracy, needs a matching acknowledgement. Which paths can *receive* one differs
([transformation-acknowledgement.md](transformation-acknowledgement.md)):

| Path (matrix rows) | Where the acknowledgement comes from |
|---|---|
| 1-6, 9, 10, 13, 16, 19 (stored rasters: footprint, tiles, pixel lookup, measurement, alignment, 3D) | Only the georeferencing version in use. A file-georeferenced raster or an older recorded version has none, so these fail closed with an explanation. A request cannot supply one. |
| 2, 4, 17 (recording georeferencing, control points) | The request that records the new version; stored with it. |
| 18 (area of interest) | The request that creates or changes the area; stored in `aoi_source`. |
| 7 (point calculator) | That request only; nothing is stored. |
| 23 (offline satellite mock) | Exact only. |

Row 1 stores the footprint of a raster that crosses longitude 180° split into two parts and flagged. Rows 5, 6, 9, 10, 13, 16 and
19 refuse such a raster with `ANTIMERIDIAN_CROSSING_UNSUPPORTED` (layer list, tiles, pixel lookup both ways, measurement,
alignment, 3D). Rows 11, 12, 14, 15, 21 and 22 are unaffected.

## What changed in behaviour

- **One converter.** `geopoc/geo/convert.py` asks the planner and applies exactly the operation it allows. The same
  coordinates therefore give the same longitude/latitude in the pixel lookup, the point converter, the layer outline, a
  measurement, the area of interest and the 3D position, with the same provenance record (`operation.id`, class, documented
  accuracy, PROJ version, chosen by selection or as the only option).
- **Refusals are the same everywhere.** Exact: used. One registered shift: used, accuracy shown. Several: refused until one is
  chosen (`TRANSFORMATION_SELECTION_REQUIRED`, with the candidates). Ballpark only: refused
  (`TRANSFORMATION_BALLPARK_BLOCKED`). Needs an uninstalled grid: refused, naming it (`TRANSFORMATION_GRID_MISSING`). Nothing
  falls back to PROJ's default.
- **A refusal is actionable, not a coordinate.** A request that needs the position fails with 409 and the options. A result
  whose main value does not need it (pixel values, a distance, the 3D surface) still returns, with the longitude/latitude
  left out and the reason beside it (`lon_lat_note`, `points_lonlat_note`, `positioning`).
- **A choice recorded with the georeferencing wins.** For a raster whose georeferencing a person recorded, the transformation
  chosen then is used by every path; a different request-level choice cannot override it. For a file-georeferenced raster the
  choice is given per request (`operation`) and is not stored (see the persistence proposal).
- **Geographic rasters on another datum are no longer assumed to be WGS 84** in the 3D position (row 19).
- **Area of interest** accepts `transformation_operation`; the stored provenance now records the operation. Existing areas of
  interest are not recomputed or rewritten.
- **Guard test.** `tests/test_transform_consistency.py` fails if any module outside a reviewed allow-list builds a PROJ
  `Transformer` directly, so a new default-PROJ conversion cannot appear unnoticed.

## Remaining default-PROJ paths

Only locators, never results:

- `geo/transform.locate()`, `ingestion/raster.py`'s first pass, and the area-of-use warning in `/crs/transform`. PROJ's default
  is used to find roughly where a point is, so that only the registered operations that apply there are listed. Nothing in a
  response is computed with it.

Everything else that builds a `Transformer` is datum-preserving by construction (rows 12 and 24) or a parity probe, and is
listed in the guard test: `geo/orientation.py` converts a CRS to its own geographic CRS (same datum) to measure local ground
directions, and `geo/projenv.py` compares pyproj's PROJ with GDAL's on fixed datum-free pairs. Neither produces a result.

## Not covered, and why

- Transformations to a vertical datum: the application does not convert heights and assumes no vertical datum.
- Conversion to any CRS other than WGS 84 longitude/latitude and Web Mercator for display: `/crs/transform` supports any
  registered pair through the planner; no other path needs one.
- A persisted choice for file-georeferenced rasters: [transformation-choice-persistence-proposal.md](transformation-choice-persistence-proposal.md).
- Nothing here defines or validates an Everest-56, Polyconic or other ESM CRS, and **no ESM transformation support is
  claimed**. A CRS is only what a file, a registry code, or a person-recorded version states.
