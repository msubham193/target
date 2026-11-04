# Map sheets (OSM and ESM): intake, metadata assessment, georeferencing and overlay

OSM/ESM work, Phase 2 (see [osm-esm-implementation-assessment.md](osm-esm-implementation-assessment.md) and
[crs-assessment-and-transformations.md](crs-assessment-and-transformations.md)). It adds **no database migration and no
schema change**. Everything below is stored in tables that already existed. The design that would be needed for a first-class
map-sheet model is a proposal only: [map-sheet-data-model-proposal.md](map-sheet-data-model-proposal.md).

Defence Series Maps and restricted defence mapping workflows are out of scope and are not offered anywhere.

## The rule everything follows

A sheet is placed only by **its own file metadata**, **a world file / .prj a person reviewed and confirmed**, or **control
points a person supplied**. Nothing is placed from the series label, the file name or the look of the picture. A series
(OSM, ESM) is a label the uploader gives; it is recorded as `declared_by_uploader` and changes no fact about the sheet's CRS,
datum, projection or transformation. "Everest-56", "Polyconic" and "Lambert Conic Conformal" are names, not CRS definitions:
no such definition is installed, assigned or guessed, and no Everest-56 transformation exists in this application.

## What was added

| Area | What it does |
|---|---|
| Intake | `POST /api/v1/projects/{id}/map-sheets` takes a GeoTIFF, a scanned TIFF / PNG / JPEG, or a PDF, with a declaration (series, sheet label, scale, edition). The original is stored unmodified (same streaming, checksum and duplicate rules as any upload). The page is **Upload & Ingestion → Map sheets**. |
| Listing | `GET /projects/{id}/map-sheets`: every sheet, how it is positioned, and what it still needs. |
| Metadata assessment | `GET /assets/{id}/map-sheet`: what the file states about datum, ellipsoid, prime meridian, projection, parameters, units, coordinate grid, extent and sidecars, each marked *stated by the file*, *recorded by a person*, *from the PROJ registry* or *unknown*; what is unresolved and why; what information would resolve it. |
| Unresolved metadata | `POST /assets/{id}/map-sheet/metadata-notes`: a person records what they found (for example a datum named in the margin) and where it came from. It is a record for review and is **never applied**. |
| Georeferencing | World file + `.prj` use the existing sidecar review. Scanned pictures use the new **control-point** workflow (`POST /assets/{id}/control-points/validate` and `.../control-points`). |
| Overlay | The map lists map sheets and rasters together; layers can be reordered, hidden and faded, and two layers can be compared (`GET /projects/{id}/alignment`). |

## Formats

| Input | What happens |
|---|---|
| Georeferenced GeoTIFF | Read like any raster. Placed from its own CRS and geotransform, through the Phase 1 transformation planner (exact / approximate / blocked ballpark / choice required). |
| TIFF with world file and `.prj` | Stored unplaced. Sidecars are added from the asset page or with the ordinary raster upload (they pair by file name), then reviewed, validated and confirmed by a person; only then is it placed. A malformed sidecar is kept and never applied. |
| Scanned TIFF, PNG, JPEG | Stored unplaced (`MAP_SHEET_SCANNED`). A preview is made. Place with control points. World files for PNG/JPEG are not paired by file name; paste the world file text in the georeferencing panel, or use control points. |
| PDF | Stored. Its structure is scanned (version, page count, first-page size, embedded pictures, GeoPDF markers) without rendering. **It cannot be drawn or georeferenced here**: the bundled GDAL cannot read PDF and no renderer is installed (nothing is downloaded). Convert a page to TIFF or PNG elsewhere and upload that. Georeferencing a PDF claims (`/GEO` measure, corner points, EPSG number) is shown as *unverified evidence* and is not applied. |

## Control points

A control point is a pixel position (column, row; 0, 0 is the top-left corner of the picture) with a coordinate in a CRS the
person chooses from the local registry. The application fits the six affine numbers by least squares and reports every
residual; it does not find the points on the sheet and does not know the sheet's own projection.

Everything is judged **on the ground, in metres**, from the picture's own resolution, not in pixels or map units (a pixel is
10 m for one scan and 0.5 m for another, a degree is not a metre, and the two pixel sides need not be equal). The geometry is
measured numerically at the sheet (`geopoc/geo/orientation.py`), so it is right for any axis order, direction and unit:

- `g = sqrt(|u| * |v|)` is the ground size of a pixel (`u`, `v`: the ground vectors of one column and one row step);
- `sigma = sqrt((p*g)^2 + s_ref^2 + s_src^2)` is the expected error of one coordinate of one point. `p` is how closely a person
  can place a point (1 pixel). `s_ref` (uncertainty of the reference coordinates) and `s_src` (of the printed source) are what
  the person states, **never assumed**: with none stated they are zero and the result says "not stated";
- a 2-D residual is expected to be `sqrt(2) * sigma`. A **warning above 2x that, a failure above 5x**. The residual is the RMS
  allowing for the six fitted numbers (`n` points leave `2n - 6` free observations). A 10 m pixel therefore warns above about
  28 m and fails above about 71 m; a 0.5 m pixel warns above about 1.4 m. There is no universal pixel or metre threshold.

Checks (constants in `geopoc/geo/controlpoints.py`, defaults only):

- at least 3 points, all inside the picture, finite, and distinct (two sharing a pixel position or a coordinate are named and
  refused). **Exactly three points are allowed** and carry a permanent warning, `EXACT_FIT_NO_CHECK`: they fit with no residual,
  so the fit cannot reveal a wrong point and its accuracy cannot be checked; a zero residual means nothing;
- **leave-one-out** (four or more points): each point is left out in turn and the others place it; the miss is reported in
  metres beside the ordinary residual. A point whose removal leaves the rest unable to fix the sheet is named (the result is
  "partial") and cannot be vouched for by the others. With three points it is not available, and says why;
- the points must not lie on one line (refused) or nearly so (warning); clustered points (hull under 25 % of the picture) warn
  that positions are extrapolated; **uncertainty at the sheet's four corners** is reported from the fit's leverage;
- **shear** (the pixel sides not perpendicular on the ground) is judged by what it does: a warning when it moves the far edge of
  the sheet by more than the warning tolerance, a failure beyond the failure tolerance. It is not confused with rotation;
- pixel shape on the ground: more than 5 % unequal sides warns and more than 25 % fails;
- a **mirrored** picture (rows running north on the ground) warns, found from the ground geometry and not from the sign of a
  number, so it is right for CRSs such as EPSG:2046 (x west, y south) and EPSG:2193 (northing listed first);
- **rotation** of more than 5° is reported for information, against the CRS's own "up" axis, and the full rotation is applied.
  A polar grid is judged against its grid, not true north;
- then every ordinary georeferencing check runs on the result (CRS resolves, coordinates fit the CRS, area of use, tile
  renders) **and the transformation rules apply**: a CRS with only a ballpark fallback is refused, one with several
  registered datum shifts needs the person's choice, and a shift worse than 10 m (or with no stated accuracy) needs the
  person's acknowledgement ([transformation-acknowledgement.md](transformation-acknowledgement.md)).

Nothing is stored until the person reviews the fit and confirms. The recorded version holds the points, the CRS, the stated
source, the residuals in pixels and metres, the tolerance and how it was derived, the leave-one-out result, the corner
uncertainty and the software versions (`georef_assertion.validation.control_points`, `evidence_schema: 2`), with the person
and reason. Versions recorded before this keep their schema-1 evidence (pixel thresholds, no ground assessment) and are shown
as they were.
The original file is unchanged and the file's own (missing) metadata stays visible.

A fit across a whole sheet in one affine map is an approximation. If the sheet is printed in a projection other than the CRS
chosen, residuals are not zero. That is reported, not corrected, and is never evidence that the chosen CRS is the sheet's.

## Where records are kept (no schema change)

| Record | Stored in |
|---|---|
| Original bytes | `blob` + file store (unchanged) |
| Declaration, intake kind, processing steps | `asset.metadata["map_sheet"]` (written once at upload) |
| PDF structure facts | `asset.metadata["pdf"]` |
| Georeferencing from a sidecar or control points | `georef_assertion` (append-only). Control-point evidence in `validation["control_points"]`; method is the existing `geotransform_entered` |
| Unresolved-metadata notes | `audit_event` (append-only), action `map_sheet.metadata_note` |
| Intake and control-point audit | `audit_event` |

PDF map sheets are `overhead_raster` assets with no `raster_metadata` row, because the asset modality is constrained to three
values. The proposal explains why this is a stopgap.

## ESM metadata assessment: what it will and will not say

For each of datum, ellipsoid, prime meridian, projection, projection parameters, units, coordinate grid, geographic extent and
the transformation to WGS 84 the assessment says whether the file states it, and the value if so. A sheet with no usable CRS
reports every one as *unknown* and lists what is needed. For a sheet whose file does state a CRS in the Everest / Indian /
Kalianpur, Polyconic or Lambert Conic Conformal families, the assessment adds a *names are not definitions* hint, marks the
datum as *needs an authoritative source*, and shows that the registry holds only a blocked ballpark fallback. An ESM
declaration adds an *unverified* item: other ESM sheets are not assumed to share these parameters.

Information still required from the client before any ESM transformation can be claimed:

1. An authoritative written definition (datum, ellipsoid variant, projection, every parameter, units) with its source document.
2. Published datum-shift parameters or grids to WGS 84, with documented accuracy and area of validity.
3. Confirmation whether each sheet or only the series shares one parameter set.
4. Real sample sheets, with margin text, to check control points and the printed grid against the definition.

## Rasters that cross longitude 180°

A raster whose footprint runs through the antimeridian is detected (it is not reported as a bad CRS coordinate), its outline is
unwrapped, checked, and **split at 180° into two parts**, and the footprint's record says so
(`crosses_antimeridian`, `footprint_representation: split_multipolygon`, `split_boundary_degrees: 180`). A footprint that only
touches 180° or -180° is not a crossing. Only the footprint is supported. Drawing tiles, measurement, areas of interest,
terrain, pixel lookup by position and alignment refuse such a raster with `ANTIMERIDIAN_CROSSING_UNSUPPORTED` and say why;
nothing is dropped silently. The flag lives in the footprint's JSON record for a file-georeferenced raster; for a stored footprint
elsewhere it is derived from the geometry (a MultiPolygon with parts touching +180 and -180). A rotated raster across 180° and a
polar raster were not exercised.

## Tests and what they do not show

All fixtures are synthetic and generated in memory (`tests/test_map_sheets.py`, `test_map_sheet_formats.py`,
`test_control_points.py`, `test_esm_metadata.py`, `test_alignment.py`, frontend `mapSheet`, `controlPoints`, `mapSheets`
tests and `e2e/sheets.spec.ts`). They show how the application behaves. **They do not show that real OSM or ESM sheets are
supported**, and no ESM or Everest-56 compatibility is claimed. The overlay pixel test draws one ground rectangle from a UTM
raster and from a WGS 84 raster and requires the covered tile pixels to coincide; it says nothing about any real sheet's
accuracy.

## Remaining limitations

- **CRS consistency** was completed in Phase 3: pixel lookup, measured points, the area of interest and the 3D position now
  use the transformation planner too (see [crs-conversion-coverage.md](crs-conversion-coverage.md)).
- The datum choice for a raster using its own file georeferencing lasts for the current map view only; persisting it needs a
  schema change. For the same reason a file-georeferenced raster whose only datum shift is worse than 10 m cannot be
  acknowledged and stays blocked ([transformation-acknowledgement.md](transformation-acknowledgement.md)).
- PDFs cannot be rendered or georeferenced here.
- The declaration (series, label, scale, edition) cannot be corrected after upload; there is no append-only history for it.
- Notes about unresolved metadata live in the audit log and are not structured, searchable records.
- A single affine fit does not model polyconic or conic distortion; there is no sheet-corner (neatline) or graticule model.
- The control-point tolerance is only as good as the uncertainties a person states. With none stated it judges placement
  alone (one pixel); a printed source with a coarse graphical accuracy needs `source_uncertainty_m` to be judged fairly.
- **No real OSM or ESM sheet has been checked.** [map-sheet-validation.md](map-sheet-validation.md) describes the offline
  harness that will do it, and reports NOT VALIDATED until real, authorised samples are supplied.
- Printed marginalia, grid lines and graticule ticks are not read; control points are entered by a person.
- No mosaics, sheet index, adjacency, or tiling of a series.
- Alignment inspection compares footprints and documented accuracies; it does not measure feature alignment from pixels.
- Existing assets cannot be re-declared as map sheets.
