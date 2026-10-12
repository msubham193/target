# Asset Ingestion

How files get into a project: what is accepted, how it is checked, where it is stored, and what happens when something is wrong. Implemented in Phase 2.

## Supported formats and limits

Each kind of input works on its own. A project never needs all three.

| Kind of input | Accepted extensions | Content must be | Limit per file |
|---|---|---|---|
| Ground photographs | `.jpg`, `.jpeg`, `.png`, `.webp` | JPEG, PNG or WebP | 50 MB |
| Satellite / aerial raster | `.tif`, `.tiff` | TIFF / GeoTIFF readable by GDAL's GTiff driver | 1 GB |
| Video | `.mp4`, `.mov`, `.mkv` | MP4/MOV (ISO base media) or Matroska, with a video stream FFmpeg can decode | 1 GB |

MB and GB are binary (1 GB = 1024³ bytes). The limits bound what is accepted. They do not promise that every accepted file can be processed later.

Ground photographs are also limited to 100 million pixels, because an image is fully decoded to prove it is readable.

`GET /api/v1/ingestion/rules` returns the extensions and limits the server is enforcing; the upload screen reads them from there.

## How a file is checked

In order. The first failure stops the upload.

1. **Project and profile.** The project must exist and not be archived; a local profile must be selected.
2. **Extension** must be one accepted for the chosen kind of input.
3. **Declared size** (`Content-Length`) must be within the limit, and the data drive must keep its reserve of free space.
4. **Streaming.** The body is written to a temporary file in 64 KB-scale chunks while its SHA-256 is computed. The size limit is enforced again on the bytes actually received.
5. **Signature.** The first 16 bytes must match the format the extension claims. A PNG named `.jpg` is refused.
6. **Readability.** The file is actually opened:
   - images are fully decoded with Pillow;
   - rasters are opened with Rasterio and two 512-pixel windows are read (first and last corner), which catches truncation without loading the raster;
   - videos are probed with `ffprobe` and one frame is decoded with `ffmpeg`.
7. **Metadata** is extracted. Anything that cannot be established is recorded as unknown with the reason.
8. **Storage and database.** The original is moved into place and the asset, its metadata and an audit record are committed together.

The file is never held in memory as a whole. A 400 MB GeoTIFF was ingested with the API process peaking at 129 MB (113 MB before the upload).

## Upload API

```
POST /api/v1/projects/{project_id}/assets?modality=<kind>&filename=<name>
Content-Type: application/octet-stream
X-Workspace-Profile: <profile id>

<raw file bytes>
```

The body is the raw file, not multipart, so it can be streamed straight to the data drive. A multipart upload would first be spooled to the system temp folder on C:, which is nearly full.

Other endpoints: `GET /api/v1/projects/{id}/assets` (list, filter by `modality` and `status`, cursor pagination), `GET /api/v1/assets/{id}` (metadata), `GET /api/v1/assets/{id}/content` (the original bytes, with HTTP Range).

## Checksums and duplicates

Every asset has a SHA-256 of its exact bytes. Policy: **accept, link and flag**.

- Uploading content that is already in the project creates a new asset record (kept for audit), sets `duplicate_of_asset_id` to the **first** asset with that content, and adds the `DUPLICATE_ASSET` flag. The response says `duplicate: true`.
- The bytes are stored once. A second copy is never written (`stored_original_reused: true`).
- The same content in a different project reuses the stored original but is not flagged as a duplicate of anything in that project.
- All assets with the same checksum are one source. Later phases must count them as one when assessing corroboration; the shared `sha256` is what makes that possible.

Only byte-identical files are detected. A re-encoded or resized copy has a different checksum and is not recognised as a duplicate.

## Storage

Configured by `GEOPOC_DATA_ROOT` in `.env` (default `D:/geopoc-data`).

```
<DATA_ROOT>/
  originals/<aa>/<bb>/<sha256>    one file per unique content; written once, then read-only
  tmp/uploads/<random>.upload     uploads in progress
```

- The storage path comes only from the checksum. The client's file name is kept as metadata and never used as a path.
- Originals are never modified, converted or overwritten.
- Temporary files are deleted when an upload finishes or fails. Leftovers older than 24 hours are removed when the API starts.
- An upload is refused if it would leave less than 1 GB free on the data drive.
- If a stored original has gone missing, uploading the identical file again restores it.

Related settings (all `GEOPOC_` environment variables, optional): `MAX_GROUND_IMAGE_BYTES`, `MAX_RASTER_BYTES`, `MAX_VIDEO_BYTES`, `MAX_IMAGE_PIXELS`, `STORAGE_RESERVE_BYTES`, `MEDIA_TOOL_TIMEOUT_S`.

## Metadata recorded

| Kind | Recorded |
|---|---|
| All | File name, detected format, media type, size, SHA-256, validation status, quality flags, unknowns, uploader, ingestion time |
| Ground photograph | Width, height, colour mode, frame count, a raw sharpness indicator, selected EXIF fields, whether GPS tags are present |
| Raster | Driver, width, height, bands, data types, nodata, CRS (status, registry code, name, WKT), geotransform exactly as stored, pixel size, bounds, footprint |
| Video | Container, codec, frame size, duration, frame rate, time base, frame count, container creation time, audio present |

### What is deliberately not inferred

- **Location of a ground photograph or video.** None is assigned. EXIF GPS tags are noted as present but their values are not stored or used.
- **Capture time.** An EXIF capture time is stored as a timestamp only when the file also states its UTC offset. Otherwise it stays raw metadata and the capture time is unknown. A video's container creation time is kept but is not treated as the capture time.
- **Blur.** The sharpness number is a raw measurement with no threshold. It makes no judgement.

### Raster georeferencing

A raster is `georeferenced` only when all three hold:

1. its CRS is present and is a geographic or projected CRS tied to the Earth;
2. it has a usable geotransform (finite, non-zero pixel size);
3. its outline, transformed to EPSG:4326, lands on valid coordinates.

| Situation | `crs_status` | `georeferenced` | Flags |
|---|---|---|---|
| Valid CRS and geotransform | `valid` | true | — |
| No CRS in the file | `missing` | false | `MISSING_CRS`, `NOT_GEOREFERENCED` |
| CRS is an engineering/local or unreadable definition | `invalid` | false | `INVALID_CRS`, `NOT_GEOREFERENCED` |
| Valid CRS, no geotransform | `valid` | false | `NOT_GEOREFERENCED` |
| Valid CRS, coordinates outside its domain | `valid` | false | `INVALID_GEOREFERENCING`, `NOT_GEOREFERENCED` |

When a raster is not georeferenced it has no footprint, no bounds and no pixel units, and the reason is listed under its unknowns. No CRS, zone or position is ever assumed. A database constraint enforces that a footprint can exist only for a georeferenced raster. The raster is still accepted and can be inspected in image space.

Ground control points and RPCs are counted but not used.

## Validation status

- `validated` — readable, metadata extracted.
- `validated_with_warnings` — readable, but something needs attention: a missing or invalid CRS, untrusted georeferencing, or metadata that could not be read.

Files that fail a check never become assets.

## Failure behaviour

Every error is a JSON problem response with a stable `code`, a readable message and the request ID. Rejections are also written to the audit history (`asset.upload_rejected`) with the file name, kind of input, code and, when the whole file was received, its checksum.

| HTTP | Code | When | Stored? |
|---|---|---|---|
| 401 | `PROFILE_REQUIRED` | No local profile selected | No |
| 404 | `PROJECT_NOT_FOUND` | Unknown project | No |
| 409 | `PROJECT_ARCHIVED` | Project is archived | No |
| 413 | `FILE_TOO_LARGE` | Over the limit, by header or while streaming | No |
| 415 | `UNSUPPORTED_FILE_TYPE` | Extension not accepted for this kind of input | No |
| 415 | `SIGNATURE_MISMATCH` | Content is not what the extension claims | No |
| 422 | `EMPTY_FILE` | Zero bytes | No |
| 422 | `ASSET_UNREADABLE` | Corrupt, truncated or undecodable | No |
| 422 | `IMAGE_TOO_MANY_PIXELS` | Image above the pixel cap | No |
| 400 | `UPLOAD_INTERRUPTED` | Connection dropped mid-upload | No |
| 500 | `STORAGE_ERROR` | Could not write to or read from the data folder | No |
| 503 | `DATABASE_UNAVAILABLE` | Database not reachable | No |
| 503 | `MEDIA_TOOLING_UNAVAILABLE` | `ffprobe`/`ffmpeg` not installed, so video cannot be validated | No |
| 507 | `INSUFFICIENT_STORAGE` | Not enough free space on the data drive | No |

A failed upload leaves nothing behind: no asset row, no stored original, no temporary file. If the database fails after the original was moved into place, the file is removed again.

## Requirements on the machine

- FFmpeg (`ffmpeg` and `ffprobe`) on `PATH`, for video only. `/health` reports whether they are found.
- Rasterio and Pillow are installed with the project. Their versions, and GDAL's, are reported by `/health`.

## Not part of ingestion yet

Previews and thumbnails, map tiles and Cloud-Optimized GeoTIFF derivatives, format conversion, frame extraction, near-duplicate detection, deleting assets, resumable uploads, a background job worker, and any analysis.
