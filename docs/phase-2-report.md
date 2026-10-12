# Phase 2 Report — Asset Ingestion

Date: 2026-10-12. Phase 2 is complete at its gate. Phase 3 has not been started.

**Exit criterion (specification V2 §13): single-modality ingestion works for all input types — met.** Ground photographs, GeoTIFF rasters and video each upload, validate and persist independently.

The Phase 1 baseline was committed first as `17c37e9` (83 files; `.env`, data, caches and build output excluded; no secret found in the committed files). **Phase 2 changes are in the working tree and are not committed.**

## 1. What was built

| Area | Delivered |
|---|---|
| Upload | `POST /api/v1/projects/{id}/assets` — raw streamed body, SHA-256 while streaming, size limit enforced by header and by bytes received |
| Validation | Extension, 16-byte signature, then real readability: full image decode (Pillow), windowed raster read (Rasterio), probe plus one decoded frame (FFmpeg) |
| Duplicates | Accept, link to the first copy, flag `DUPLICATE_ASSET`; content stored once |
| Storage | Originals on the D: data folder under a checksum-derived path, written once, read-only |
| Metadata | Image, raster and video metadata; explicit unknowns with reasons; quality flags |
| Georeferencing | CRS status and a `georeferenced` verdict; footprint only when valid; a database constraint forbids a footprint otherwise |
| Retrieval | Asset list (filters, pagination), asset detail, original download with Range, accepted-format rules |
| Audit | `asset.upload` and `asset.upload_rejected` records |
| UI | Upload screen (three tabs, drop zone, queue with progress, stored / duplicate / not accepted / failed states), asset list, asset detail |
| Docs | [ingestion.md](ingestion.md), [architecture/observation-schema-notes.md](architecture/observation-schema-notes.md) |

## 2. Migrations

`0002_phase2_assets` (revises `0001`): tables `blob`, `asset`, `raster_metadata`, `video_metadata`, with check constraints on modality, status, CRS status, checksum format, and "no footprint or pixel units unless georeferenced". Applied to the working database (`alembic current` → `0002 (head)`). Tested up, down and up again on an empty database.

## 3. Dependency changes

| Package | Version | Why |
|---|---|---|
| `rasterio` | `==1.4.3` (bundles GDAL 3.9.3) | GeoTIFF reading. **1.4.4 is blocked by Windows Smart App Control on this machine; 1.4.3 was load-tested (write and read a GeoTIFF) before being adopted.** |
| `pillow` | `==10.4.0` | Image decoding and EXIF |

Not added: **OpenCV.** It was approved but turned out to be unnecessary for this phase: Pillow covers images and the installed FFmpeg covers video. Leaving it out avoids another native library that Smart App Control would have to allow.

No frontend dependency changed. No AI model, CUDA build or inference code was installed or written. Smart App Control was not modified.

Disk: C: had 6.9 GB free before installing; the packages went to the D: cache.

## 4. Test results

Run through `scripts\check.ps1` on 2026-10-12.

| Check | Result |
|---|---|
| Backend lint and format | Pass (46 files) |
| Backend tests | **109 passed**, 0 failed, 0 skipped |
| Frontend type-check | Pass |
| Frontend unit tests | **29 passed** (3 files) |
| Frontend build | Pass |
| Browser end-to-end (Edge) | **6 passed** |

### Required test areas

| Area | Result | Where |
|---|---|---|
| Each supported input category | Pass | JPEG, PNG, WebP; GeoTIFF; MP4, MOV, MKV |
| Unsupported extensions | Pass | 8 cases, including a TIFF offered as a ground photo and `.jpg.exe` |
| Mismatched file signatures | Pass | 8 cases, e.g. PNG named `.jpg`, text named `.tif`, executable header named `.mp4` |
| Oversized uploads | Pass | Refused by header, refused mid-stream without a header, accepted exactly at the limit |
| Corrupt or unreadable files | Pass | 8 cases across all three kinds (spec test T-01) |
| Duplicate uploads and checksum consistency | Pass | Three copies → three records, one stored file, all linked to the first |
| Metadata extraction failures | Pass | EXIF reader forced to fail → asset kept, gap recorded |
| GeoTIFF with valid georeferencing | Pass | CRS, geotransform, pixel size, units, footprint; also a rotated raster |
| Raster with missing or invalid CRS | Pass | No CRS (with and without geotransform); local engineering CRS; CRS without geotransform; coordinates outside the CRS domain |
| Video metadata and duration | Pass | Duration within 150 ms of 1 s, 10 frames/s, codec, size |
| Database failure | Pass | Unreachable database → 503; commit failure during upload → 503 and no orphan file |
| Storage failure | Pass | Write failure → 500 and rollback; low disk space → 507 |
| Project-to-asset association | Pass | Listing is per project; unknown and archived projects refused |
| Persistence after restart | Pass, with a caveat | See below |
| Audit records | Pass | Accepted and rejected uploads, with actor, request ID and checksum |
| Frontend upload and validation error states | Pass | 13 component tests plus the browser test |
| API contract | Pass | Generated requests against every documented operation |

### Caveats on what was actually exercised

- **Restart** was simulated inside the test process: the database connection pool was discarded and a new application object built, then the asset and its bytes were read back. The API process itself was not stopped and started by a test. The browser test additionally confirms data survives a page reload.
- **Database failure** used an unused port and a forced commit error. The database container was not stopped.
- **Storage failure** was injected by replacing the storage function. No real disk fault was produced.
- **Video fixtures** are 1-second MPEG-4 test patterns generated by the local FFmpeg. H.264/H.265 files and long or variable-frame-rate recordings were not tested.
- **Large files:** one manual run uploaded a 400 MB GeoTIFF through the running server in 2.3 s; the serving process peaked at 129 MB against 113 MB before. Nothing near the 1 GB limit was uploaded, and no large video was tried.
- **Raster fixtures** are small synthetic GeoTIFFs. No real satellite product, BigTIFF or multi-gigabyte file was tested.
- **Not run:** a backend static type-checker (none configured); accessibility checks (Phase 6).

Specification tests touched by this phase: T-01 (corrupt image) passes. T-03 and T-04 pass for their ingestion half only (CRS flagged or retained); their measurement and transformation-logging halves belong to later phases. T-06 passes for duplicate detection only; independent-source counting belongs to the aggregator. No other T-test is claimed.

## 5. Changed files

**New (backend):** `geopoc/storage/store.py`, `geopoc/ingestion/{rules,image,raster,video,service}.py`, `geopoc/api/routers/assets.py`, `geopoc/api/pagination.py`, `migrations/versions/0002_phase2_assets.py`, `tests/test_assets.py`, `tests/media.py`.

**Changed (backend):** `pyproject.toml`, `uv.lock`, `geopoc/config.py`, `geopoc/db/models.py`, `geopoc/api/{main,errors,schemas}.py`, `geopoc/api/routers/health.py`, `geopoc/devtools.py`, `openapi.json`, `tests/{conftest,test_health,test_migrations}.py`. Formatting-only changes touched other backend files because the line length was raised from 110 to 120.

**New (frontend):** `src/pages/{IngestionPage,AssetsPage,AssetDetailPage}.tsx`, `src/pages/ingestion.test.tsx`, `src/lib/ingestion.ts`, `e2e/phase2.spec.ts`.

**Changed (frontend):** `src/App.tsx`, `src/api/{client.ts,schema.d.ts}`, `src/pages/{ProjectLayout,ProjectOverviewPage}.tsx`, `playwright.config.ts`, `e2e/phase1.spec.ts`.

**Docs:** `docs/ingestion.md`, `docs/phase-2-report.md`, `docs/architecture/observation-schema-notes.md` (new); `docs/architecture/decision-log.md`, `README.md` (updated). The specification DOCX is unchanged.

## 6. Limitations

1. **Validation runs inside the upload request.** There is no background job worker yet. This is fast for the supported checks, but the job lifecycle in the specification (queued, validating, cancel, retry) is not implemented. It was in the original Phase 2 plan and is deferred because this phase was scoped to ingestion and metadata.
2. **No previews, thumbnails, map tiles or COG derivatives.** Also in the original plan; deferred for the same reason.
3. **Only byte-identical duplicates are detected.** Re-encoded or resized copies are not.
4. **A raster is checked at two corners, not everywhere.** Damage in the middle of a large raster would not be found at ingestion.
5. **Only one video frame is decoded.** Damage later in a video would not be found at ingestion.
6. **Ground control points and RPCs are not used** for georeferencing; such rasters are reported as not georeferenced.
7. **A user cannot yet assert a CRS** for a raster that lacks one.
8. **Assets cannot be deleted** through the application.
9. **An oversized upload is refused as soon as its size is known**, which can close the connection before the browser finishes sending. The upload screen checks size and extension first so users normally see the clear message rather than a dropped connection.
10. **Uploads are not resumable.**
11. **Local profiles are still not authentication**, and the API still binds to loopback only.

## 7. Unresolved issues

| Issue | Needs |
|---|---|
| **Review vocabulary.** The approval lists four values; the V3 schema it adopts has six (`needs_review` and `rejected` are the difference, and several rules rely on them). | Your confirmation — see [observation-schema-notes.md](architecture/observation-schema-notes.md) §2. Nothing is implemented either way. |
| V2-only fields to keep, map or drop | Your ruling on the table in the same note, §3 |
| Whether the deferred job worker and previews should be done before or within Phase 3 | A decision; Phase 3 pipelines will need the worker |
| Version 2.0 / 3.0 labels inside the specification | Still recorded as unresolved |
| `D:\geopoc\docs` leftover copy from Phase 1 | Yours to delete |

Phase 3 additionally needs the model decisions already listed in the decision log (U-001, U-004, U-005, U-010, U-015) and verification of a CUDA-enabled PyTorch on this GPU.
