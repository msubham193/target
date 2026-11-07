# 3D terrain viewer (first version)

Status (2026-10-29): implemented and tested on scratch resources only. No migration, no new dependency, no download, no online
service. The working database was not touched, and the running API and web builds are older, so a restart is needed to see it
(not done).

The viewer draws **elevation rasters** that are already in a project. It is a viewer of terrain data, not a source of it: it
never generates, fills in or guesses a surface. It does not reconstruct 3D from photographs (see
[photogrammetry-feasibility.md](photogrammetry-feasibility.md)).

## 0. Two views on the 3D Terrain screen

The screen has two tabs:

- **Online 3D (Mapbox)** – the project's location in 3D from Mapbox: satellite imagery draped on Mapbox's terrain model,
  buildings raised where Mapbox has a height for them, hill shading (on by default) and height lines (contours, off by
  default) from Mapbox's terrain, sky and haze at the horizon, the project's located photographs as markers,
  tilt/rotate, a height exaggeration slider (1×–5×, default 1.5×), labels, top-down, reset and full screen. It starts on
  the project's area of interest, else the confirmed photograph positions (street-level, zoom 15), else the extent of
  the placed rasters; with none of these the person chooses a place (place search; a chosen place opens further back,
  zoom 12.5, so the hills around it are seen from above). It is **online** (the view goes to Mapbox), needs the Mapbox
  public token, and everything in it – imagery, terrain heights, building heights (which may be Mapbox's estimates) – is
  Mapbox's, not measured by this app. The ground-height readout under the pointer, and the lowest–highest ground within
  about 2 km of the centre, are from Mapbox's terrain model and approximate; when that range is under 30 m the view says
  the area is nearly flat (so little relief can be seen, e.g. Bhubaneswar). Nothing is saved.
  It opens by default when a token is set and the project has no usable elevation raster.
- **Your elevation files** – the local viewer described below, unchanged. It draws only the project's own elevation
  rasters and contacts nothing.

## 1. What is supported

| Input | Supported | Notes |
|---|---|---|
| Elevation GeoTIFF, single band, `int16 / uint16 / int32 / uint32 / float32 / float64`, in a projected or geographic CRS, north-up | yes | the project's existing upload accepts `.tif` / `.tiff` only |
| The same, with the CRS or geotransform **recorded by a person** (existing georeferencing assertions) | yes | labelled "recorded by a person" |
| Declared scale and offset on the band | yes | applied as declared, and said so |
| Band declared as elevation with a unit (`metre`, `foot`, `US survey foot`) | yes, drawn at once | the file says it |
| Band that looks like elevation but the file does not say so | only after a person confirms, for that view | not stored |
| Elevation with no declared unit | only after a person states the unit, for that view | not stored; the view says it came from the person |
| Colour imagery (bands marked red/green/blue/alpha/palette), 8-bit pictures, multi-band imagery without a declared elevation band | **never** | refused, with the reason |
| A raster with no usable CRS and geotransform | no | "no map position"; record georeferencing first |
| Rotated or sheared geotransform; south-up or east-west flipped rasters | no | refused with the reason |
| A CRS in grads or any non-degree / non-length unit; compound (horizontal + vertical) CRS | no | the existing ingestion already marks compound CRSs invalid |
| More than 400,000,000 source pixels (setting `GEOPOC_TERRAIN_MAX_SOURCE_PIXELS`) | no | refused; use a smaller extract |
| ASCII grids, HGT, IMG, point clouds (LAS/LAZ), meshes, multi-file DEM tiles | no | not accepted by upload either |

"Explicitly selected local files": a file the person chooses is uploaded through the existing upload screen and becomes a project
asset (the original is stored unmodified); the viewer reads only project assets. There is no separate path that reads arbitrary
files from disk.

### How a raster is judged (reads no pixels)

For each band, from what the file declares (description, units, colour interpretation) and its data type:

* colour interpretation red / green / blue / alpha / palette, or an 8-bit band, or one of several bands without a declaration:
  **not elevation**;
* a description naming elevation (elevation, height, DEM, DSM, DTM, terrain...) or a declared length unit: **declared as elevation**
  (drawn at once if the unit is stated, otherwise the unit is asked for);
* a single numeric band that says nothing: **possible** (a person must confirm).

A raster whose bands are all "not elevation" is listed with each reason and is never drawn, even if a person insists (the API
refuses with `NOT_ELEVATION`).

## 2. What is drawn, and how the numbers are made

* The server (`GET /api/v1/projects/{id}/assets/{id}/terrain` and `.../terrain/grid`) reads the stored original, read-only, with a
  windowed **nearest-pixel decimation** to at most 256 cells per side by default (512 at most). Every value in the grid is a real
  source pixel. Nothing is averaged, smoothed or filled. The read is bounded in memory whatever the raster's size.
* The grid is sent as raw little-endian `float32`, north row first. **NaN means no data**: such cells (and cells holding the
  declared no-data value, or non-finite values) are drawn as **holes**: no triangle touches them, and the pointer reports no
  elevation there. A raster with no valid cell is refused (`TERRAIN_NO_VALID_DATA`).
* A value that is often a no-data marker (−9999, −32768, ±3.4e38) in a file that declares **no** no-data value is reported as a
  warning and left as a real elevation. It is not silently removed.
* **Horizontal scale.** Projected CRS: the CRS unit is converted to metres exactly; the raster is **not reprojected**. Geographic CRS
  (degrees): one metre size per degree is used across the whole area (geodesic, at the centre latitude) and the view says it is
  approximate.
* **Vertical scale.** Heights are the file's values; they are converted to metres only by the declared (or person-stated) unit. The
  lowest cell sits at z = 0 in the scene purely for drawing; the readout always shows the real stored value with its unit.
* **Vertical exaggeration** is a slider (×1 to ×10, default ×1 = true scale). It is stated in the control, in a badge on the picture
  itself, and in the disclosures whenever it is not ×1. It changes the picture only.
* **Vertical datum** (what zero height means) is shown as stated only if the file's CRS says so; otherwise "not stated". Heights are
  never described as heights above sea level unless the file says so.
* **Colour** is a sequential ramp by elevation (with its own legend and the data's unit), or grey shading only.
* All drawing is local WebGL 2 (a small renderer in `TerrainViewer.tsx`: no library). No terrain tiles, basemap, font, script or
  image is requested from any other address; a test records every request.

## 3. Controls and readout

* **Orbit** (drag), **pan** (shift-drag, right-drag or middle-drag), **zoom** (wheel or pinch); keyboard when the view is focused:
  arrows orbit, shift+arrows pan, + / − zoom, Home resets. Buttons: Reset view, Top view, From the north, Zoom in/out. A north arrow
  turns with the view; a frame shows the extent; the surface size and cell size are printed on the picture.
* **Under the pointer**: the cell's column and row, its native CRS coordinates (the cell centre) and the stored elevation with its
  unit, or nothing over a hole or empty space. **Click** pins a cell and gives its longitude and latitude through the existing CRS
  transform endpoint. Elevation is never interpolated between cells.
* The page lists every raster of the project with its state (Elevation / Needs confirmation / Not elevation / Cannot be drawn) and the
  reasons, and a project with no rasters, or none that are elevation, shows a plain empty state and draws nothing.

## 4. Data and storage

Nothing is written: viewing creates no job, file, asset, audit row or database row (tested by counting rows and files before and after).
A person's statement of band or unit is a request parameter for that view; it is not stored. The original's checksum is unchanged.
Everything is scoped to a project: a raster of another project is "not found".

## 5. Not done, and limits

* **No reconstruction from photographs.** See the feasibility document.
* **No overlay of imagery on the terrain** (draping an orthophoto or the project's RGB rasters), no meshes or point clouds, no
  measurements on the surface, no vertical datum conversion, no reprojection of the raster, no tiling or level of detail: one
  decimated grid of at most 512 × 512 cells is drawn. Fine detail of a very large DEM is therefore not shown; the "detail" control
  chooses 128 / 256 / 512.
* **Rendering checked in one browser** (Edge on this machine, software or hardware WebGL 2). The shaders and the renderer are
  exercised in jsdom only through a stand-in context.
* The decimation takes the pixel at the middle of each block, so a thin feature narrower than a block can be missed or caught
  depending on alignment; this is stated in the disclosures.
* Rasters in compound CRSs (horizontal + vertical) are refused because ingestion marks them invalid; supporting them would be a
  change to ingestion, not to this viewer.
* The geographic-CRS metre size is approximate across large areas (it is one value for the whole area).
* Not tested: real-world DEMs (only synthetic ones were used), rasters of hundreds of millions of pixels, other browsers or
  graphics drivers, touch input on a real device.

## 6. Tests

* Backend (`tests/test_terrain.py`, scratch database): which rasters are offered; the refusal of RGB, 8-bit and multi-band imagery
  (also by the grid endpoint when a person insists); missing CRS, rotated and south-up geotransforms, a CRS in grads, an oversized
  raster; recorded georeferencing; unit and confirmation rules (never guessed, never overridden, never stored); declared feet and
  scale/offset; exact values and decimation by real pixels; no-data, NaN, undeclared markers, all-no-data and flat surfaces; exact
  placement and bounds in UTM and the approximate metre size in degrees; grid-size bounds; project isolation; read-only behaviour.
* Frontend: geometry, mesh (holes, slopes), camera, picking (`terrain.test.ts`); the viewer with a stand-in WebGL context
  (`terrainviewer.test.tsx`); the page's empty states, confirmation flow, disclosures, readout and failures (`threed.test.tsx`).
* Browser (`e2e/terrain.spec.ts`, real Edge, scratch database): upload of three synthetic rasters, the states in the list, the
  surface drawn (colour statistics of the real canvas), orbit/zoom/keyboard changing the real camera, hover and click readouts with
  longitude/latitude, exaggeration, the unit/confirmation flow, and imagery refused.
