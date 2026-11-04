# CRS assessment and transformation transparency

OSM/ESM work, Phase 1 (see [osm-esm-implementation-assessment.md](osm-esm-implementation-assessment.md)). It adds no
database change and no stored data: everything below is computed on request from what is already stored and from the local
PROJ database. Nothing is downloaded, and no CRS is ever assigned to a raster from its name, series or appearance.

## The problem it solves

Moving coordinates between two datums is not one calculation. For the Kalianpur and Indian datums the local registry holds
several registered shifts (stated accuracy from 1 m to 999 m), and for some CRSs only a rough "ballpark" fallback. PROJ and
GDAL used to pick one silently. Now every conversion says which operation it used, how accurate that is documented to be,
and refuses the cases that cannot be trusted.

## Classes

| Class | Meaning | Used? |
|---|---|---|
| `exact` | No datum change; only a map projection (stated accuracy 0). | Yes, without confirmation. |
| `approximate` | A registered datum shift with a documented accuracy, or with none stated. | Yes if it is the only one that applies and its accuracy is **10 m or better**; if several apply, a person must choose. Worse than 10 m, or no stated accuracy: only after the person **acknowledges** that operation ([transformation-acknowledgement.md](transformation-acknowledgement.md)). Accuracy is shown everywhere. |
| `ballpark` | PROJ's fallback, recognised by its structure (`has_ballpark_transformation`), never by wording alone. | **Blocked by default**, and an acknowledgement does not unlock it. Only `/crs/transform?accept_ballpark=true` accepts it, and marks the result `BALLPARK_ACCEPTED`. |
| `unavailable` | A registered operation exists but needs a grid that is not installed. | Never. The grid is named. |

The accuracy figure is the one published with the operation in the PROJ/EPSG registry. It describes the datum shift only,
not the map sheet, the image, a hand-entered georeferencing or the choice of CRS.

Which operations apply depends on where the raster is, so they are matched to its footprint. The same pair of CRSs can be
unambiguous in one place and need a choice in another.

## What changed

- `GET /api/v1/assets/{id}/crs-assessment`: datum, ellipsoid, prime meridian, projection and parameters, units, axes, area of
  use, the eight statuses (georeferenced, CRS identified, CRS missing, CRS ambiguous, transformation available,
  transformation unavailable, georeferencing required, validation failed) and the candidate operations for footprints and for
  map tiles. Read-only.
- `GET /api/v1/crs/operations`: the classified operations between two registered CRSs, optionally for an area.
- `GET /api/v1/crs/transform`: adds `operation`, `accept_ballpark` and reports the operation used.
- Layers, tiles and georeferencing validation follow the same rules. A tile is drawn through the chosen operation (GDAL is
  given the PROJ pipeline), not through GDAL's default. `?operation=` on a tile or `operation=assetId:operationId` on the
  layer list carries a choice; georeferencing recorded by a person keeps the choice in its history (`transformation_operation`).
- New uploads: a ballpark-only or grid-missing CRS is not placed (flags `TRANSFORMATION_BLOCKED` plus the specific code); a
  registered single shift is placed and flagged `TRANSFORMATION_APPROXIMATE`; several shifts leave the raster with an
  indicative outline and `TRANSFORMATION_SELECTION_REQUIRED` until a choice is made.
- UI: a **Coordinate reference system** panel on a raster's asset page, a chooser on the map's layer list, a chooser in the
  georeferencing review, and class and accuracy labels on layers.

## Trying it by hand

Opening a raster's asset page shows the panel; it only reads. Uploading a test file stores an asset that stays (see
[acceptance-checklist.md](acceptance-checklist.md) for the safe way: a dedicated test project and synthetic files). Useful
synthetic cases, all written with rasterio and a CRS from the registry, never from a real map sheet:

| Raster CRS (placed near) | Expected |
|---|---|
| EPSG:32643 (India) | Exact; no flags. |
| EPSG:24313, Kalianpur 1962 / UTM 43N (India) | Approximate, one shift (EPSG:1247), documented accuracy 999 m, **worse than 10 m**: not placed from a file until acknowledged; flag `TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED`. A person-recorded georeferencing can carry the acknowledgement. |
| EPSG:2041, Abidjan 1987 / UTM 30N (Côte d'Ivoire) | Approximate, one shift (EPSG:1470), documented accuracy 2 m, within the limit: placed; flag `TRANSFORMATION_APPROXIMATE`. |
| EPSG:24047, Indian 1975 / UTM 47N (Thailand) | Two shifts (EPSG:1812, EPSG:1304): a choice is required; the layer is not drawn until one is chosen. |
| EPSG:24371, Kalianpur 1880 / India zone I | Only a ballpark fallback: blocked, not placed. |
| EPSG:27700 (British National Grid) | Four registered shifts (2 m to 21 m): a choice is required. A more accurate grid-based shift is listed as unavailable because its grid is not installed. |

Common CRSs on other datums (NAD83, ETRS89, GDA2020, RGF93) have a registered shift of about 1 to 4 m, so new uploads in
them are labelled approximate rather than exact. That is the registry's own statement, shown instead of hidden.

## CRS correctness hardening (Phase 2)

Decided by the project owner; no migration and no schema change.

- **Accuracy gate and acknowledgement**: a datum shift worse than 10 m, or with no stated accuracy, is not used until a person
  acknowledges it. See [transformation-acknowledgement.md](transformation-acknowledgement.md).
- **Structural ballpark detection**: a ballpark is recognised by `has_ballpark_transformation`, with the wording as corroboration
  only; a disagreement fails closed and is logged. An operation with no stated accuracy is *not* a ballpark.
- **PROJ and GDAL**: pyproj and rasterio each bundle their own PROJ (9.5.1 and 9.5.0 here) and `proj.db`. `PROJ_NETWORK` is
  forced to `OFF` when the package is imported, before any raster is opened, and pyproj's own switch is turned off with it, so an
  environment that asks to go online is overridden (and the report says it was). `/health` and the start-up log report both
  copies: versions, data directories, any `PROJ_DATA`/`PROJ_LIB` override, the EPSG database versions, and a fixed-coordinate
  **parity check** of the two engines (seven datum-free pairs, 1 mm). The EPSG databases differ here (v11.022 vs v11.016); that is
  a **warning, not a refusal**: 20 operations and one projected CRS exist only in pyproj's database (five of the operations were looked at: Canadian
  vertical), and two shared operations state different accuracies (EPSG:10661, 10662). A disagreement in the parity check is a
  failure, shown as one. GDAL's *configuration value* for the network is verified; the state PROJ holds inside GDAL cannot be
  observed with the installed API, and the report says so.
- **Antimeridian**: footprints crossing 180° are split and flagged; every other consumer refuses with
  `ANTIMERIDIAN_CROSSING_UNSUPPORTED` ([map-sheets.md](map-sheets.md)).
- **Orientation** is measured on the ground at the raster (`geopoc/geo/orientation.py`), separating the CRS's axis directions,
  the picture's pixel axes, the picture's rotation against the CRS's own "up" axis, and real shear. The old checks that read the
  sign of the pixel height or of the determinant assumed east/north; they were wrong for EPSG:2046 (x west, y south), and are
  gone. The map readout and coordinate headings name the CRS's own axes (`xy_axes` in `/crs/transform`).
- **Control points** are judged in metres from the ground resolution ([map-sheets.md](map-sheets.md)).

## Limitations

- Conversion to longitude/latitude (WGS 84) and Web Mercator only.
- Pixel lookup, measured points, the area of interest and the 3D position now follow the same rules (OSM/ESM Phase 3,
  [crs-conversion-coverage.md](crs-conversion-coverage.md)); only locators still use PROJ's default, and never produce a result.
- A choice for a raster that uses its file's own georeferencing lasts for the current map view; it is stored only when
  georeferencing is recorded. Persisting such a choice needs a schema change (a later phase).
- Rasters that were already stored are not rewritten. Their saved outline stays as it was; the layer list evaluates them again
  with the rules above.
- No Everest-56, polyconic or other map-series CRS is defined here, and no transformation grids are installed. Real ESM
  sheets and their authoritative definitions are needed before any ESM transformation can be claimed.
