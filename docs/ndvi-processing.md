# NDVI Studio: Sentinel-2 NDVI as a background job

NDVI from two locally stored Sentinel-2 L2A bands (B04 red, B08 near-infrared). It runs as a `derived_index` job in the
existing queue, stores one derived GeoTIFF through the ordinary ingestion path, and draws it on the existing map through
the existing tile route with a colour ramp.

**Status.** Code and offline tests are complete. Two things are not done and need your separate approval: **migration
0010 has not been applied** (so the live database rejects the `derived_index` job kind), and **no live browser check has
been made** (no service was started). See "Review gate" below.

## How it fits the existing system

| Need | Existing piece reused | Added |
|---|---|---|
| Background work, progress, cancel, recovery | `jobs/queue.py`, worker, `run_job` | job kind `derived_index` (migration 0010), handler `geopoc/derived_index.py` |
| Storing the result as an asset | `ingestion.service.ingest` (as the `compare` job does) | origin `derived`, flag `DERIVED_ASSET`, provenance in `extracted_metadata["derived_index"]` |
| Placing it on the map | `GET /projects/{id}/layers`, `GET /assets/{id}/tiles/...`, `MapCanvas` | optional `colormap` / `colormap_range` on the tile route (`geo/colormaps.py`) |
| Same-grid check | `geo/compare.check_alignment` | `geo/indices.file_state` (file CRS and transform in the same form) |
| Reading products | `rasterio` / GDAL already installed | `geo/indices.py` (engine), `geo/bandscan.py` (discovery) |

Nothing here creates a second queue, worker, asset store or map.

## Method

1. `MTD_MSIL2A.xml` is parsed (no DOCTYPE or entities, size capped). For each band the product's own
   `BOA_ADD_OFFSET` (matched by `band_id` through `Spectral_Information`), `BOA_QUANTIFICATION_VALUE` and the NODATA and
   SATURATED special values are read. A product without them is refused; nothing is defaulted.
2. `reflectance = (DN + BOA_ADD_OFFSET) / BOA_QUANTIFICATION_VALUE`, float64.
3. `NDVI = (NIR - Red) / (NIR + Red)`. A pixel is nodata (`-9999`) if either DN is NODATA or SATURATED, if the denominator
   is below 1e-6, or if the result is not finite.
4. **Range mask (decision: MASK).** A calculated value outside the inclusive range [-1, 1] is nodata. It is never
   clamped; negative values inside the range are kept; values exactly -1 or +1 are valid. The range is judged on the
   float32 value that is stored. The count masked is recorded.
5. Both bands must share size, CRS and geotransform. Nothing is resampled or reprojected, so the shared CRS
   transformation engine is neither needed nor bypassed; the output stays in EPSG:32755.
6. Strip-by-strip (512 rows); neither band is ever held whole. Output: tiled 256, DEFLATE, overviews 2/4/8/16
   (nodata-aware averaging), float32, explicit nodata, tags with both inputs' names, SHA-256 and scaling, the product, the
   formula, the range rule and the nodata rule.
7. The written file is reopened and validated (grid, nodata, counts, statistics, sample pixels recomputed in plain Python,
   three whole strips against a second implementation). A run whose output fails its checks is not stored.

## Data and security

* **Inputs** are not assets and are never copied or changed. They stay in the server's band folder
  (`GEOPOC_IMAGERY_BAND_ROOT`; for the proof of concept a temporary scratch folder, not storage). Only that folder is
  scanned, shallowly and boundedly, links are ignored and every file is checked to lie inside it.
* **Clients never send a path, URL or file name.** A scene is an opaque 16-hex id; responses carry file names only. A
  test fails if a path appears in any response or in the page.
* **Output** is one asset registered by `ingest` (origin `derived`). Provenance is on the asset
  (`metadata.derived_index`) and in the job's result; no lineage rows are written because the inputs are not assets, so
  the second constraint change was not needed.
* **Access.** The run routes need a workspace profile (as the job routes do) and are scoped to the project; a run is
  invisible from another project. The tile route is unchanged in how it authorises: it serves the asset by id, as it does
  for every raster. The `colormap` option only changes colours and is refused unless the raster is single-band float.
* **Duplicates.** A second run of the same scene in the same project is refused (409) while one is queued or running,
  serialised with a database advisory lock, which needs no schema. A finished scene may be run again; an identical
  result is linked to the earlier asset as a duplicate.

## API

| Route | Purpose |
|---|---|
| `GET /api/v1/indices` | The indices, formula, analytical range and legend (colour stops) |
| `GET /api/v1/projects/{id}/indices/{key}/inputs` | Scenes found in the band folder and whether each pair is compatible |
| `POST /api/v1/projects/{id}/indices/{key}/runs` | Queue a run (202). Body `{ "scene_id": "<16 hex>" }` |
| `GET /api/v1/projects/{id}/indices/{key}/runs` | Runs, newest first, with live status and the result |
| `GET /api/v1/projects/{id}/indices/{key}/runs/{job_id}` | One run |
| `POST /api/v1/projects/{id}/indices/{key}/runs/{job_id}/cancel` | Stop a run; nothing from it is kept |
| `GET /api/v1/assets/{id}/tiles/{z}/{x}/{y}.png?colormap=ndvi&colormap_range=-0.2,0.9` | Existing tile route with the optional ramp |

Asset details come from the existing `GET /api/v1/assets/{id}`.

## Real-data result (B04 + B08, tile T55HGS, 2024-01-15)

Scratch files (outside the repository, not ingested):
`ndvi_out/T55HGS_20240115T235221_NDVI_10m.tif` (raw, unchanged) and
`ndvi_out/T55HGS_20240115T235221_NDVI_10m_analytical.tif` (masked).

| | Raw | Analytical (masked to [-1, 1]) |
|---|---|---|
| Valid pixels | 117,518,474 | **117,496,393** |
| Masked out of range | n/a | **22,081** |
| Invalid (nodata) | 3,041,926 | 3,064,007 |
| Min / max | -117.0 / 105.0 | **-1.0 / 0.9886** |
| Mean / std | -0.0140 / 0.1147 | **-0.0140 / 0.0541** |

The analytical file equals the raw file with out-of-range pixels replaced by nodata, pixel for pixel (0 differences over
120,560,400). 975 pixels hold exactly -1.0 and none exactly +1.0, so the bounds are inclusive and nothing was clamped.
The sources and the raw file are byte-identical afterwards.

## Review gate: migration 0010

`services/api/migrations/versions/0010_derived_index_job_kind.py` is written and tested on scratch databases only.
**It has not been applied to the working database `geopoc`, which is still at revision 0009.**

```sql
-- upgrade (0009 -> 0010)
ALTER TABLE job DROP CONSTRAINT ck_job_kind;
ALTER TABLE job ADD CONSTRAINT ck_job_kind CHECK (kind IN ('preview', 'analyze', 'selftest', 'statistics', 'display_copy', 'compare', 'derived_index'));

-- downgrade (0010 -> 0009); refuses while any derived_index job exists
DO $$ BEGIN IF EXISTS (SELECT 1 FROM job WHERE kind = 'derived_index') THEN RAISE EXCEPTION 'derived_index jobs exist; downgrade refused'; END IF; END $$;
ALTER TABLE job DROP CONSTRAINT ck_job_kind;
ALTER TABLE job ADD CONSTRAINT ck_job_kind CHECK (kind IN ('preview', 'analyze', 'selftest', 'statistics', 'display_copy', 'compare'));
```

Impact: one CHECK constraint on `job` is replaced; no table, column, index, trigger or other constraint changes (a test
compares every column, constraint, index and trigger before and after). Every existing row satisfies the wider list (the
working database holds 30 jobs: analyze 7, preview 20, statistics 3), so the upgrade cannot fail on data. The two
statements take a brief exclusive lock on `job`. The upgrade is applied with the project's usual verified backup
(`scripts\migrate.ps1`); it is not applied by tests, by starting the API, or by this change.

## Limitations

* Only NDVI is registered; the engine is generic for any normalised difference of two bands on one grid.
* 10 m R10m bands of one product; no cloud masking (this scene is about 38 % cloud and 62 % water per its metadata), so
  values over cloud and open water are near zero or noisy and are not interpreted.
* Two simultaneous starts for one scene are serialised by the advisory lock, but there is no unique database
  constraint, which would need a schema change.
* The tile renderer fills nodata with 0 before bilinear resampling (as for every raster), so pixels at the very edge of
  a masked area can blend slightly towards the zero colour.
* The output compresses poorly (float noise): about 0.8 of raw size.
* A symbolic link out of the band folder is refused by design; that check is skipped where links cannot be created.
