# Sidecar metadata: `.tfw`/`.wld`, `.prj` and `.aux.xml`

Status: **implemented for review and confirmation of georeferencing** (owner follow-up after the Phase 3b gate, 2026-10-21). Band metadata from `.aux.xml` is parsed and shown, **not applied**. The plan written earlier the same day (K-02) is kept below as amended; §9 lists where the implementation differs from it and why.

## 1. Purpose

Some rasters carry their georeferencing, nodata or band descriptions in separate files next to the image instead of inside it:

- a world file (`.tfw`, `.tifw`, `.tiffw`, `.wld`): six numbers, giving pixel size, rotation and the centre of the top-left pixel. It gives no CRS.
- a `.prj`: a CRS as WKT, usually the older ESRI WKT1 dialect. It gives no coordinates.
- a GDAL `.aux.xml`: a PAM file that can hold a CRS, a geotransform, band metadata, statistics, metadata domains, GCPs and RPCs.

A person can now add these files and **review what each says before anything is used**. A sidecar is evidence to review, never an authority because it exists.

## 2. Rules, as implemented

1. **Originals and sidecars are never modified.**
   - Sidecars are stored unmodified, read-only and content-addressed, like originals, in the same blob store (`blob` row; `originals/<aa>/<bb>/<sha256>`, no file extension).
   - Because stored files have no extension, GDAL cannot find a sidecar next to a raster by name.
   - `GDAL_PAM_ENABLED=NO` is set when the `geopoc` package is imported (`geopoc/__init__.py`). The API, the worker and every job process import it, so GDAL neither reads nor writes `.aux.xml` files on its own.
   - A test checks that no `.aux.xml` appears anywhere under the data root after upload, preview, statistics and rendering.
2. **Nothing is applied by uploading.**
   - A sidecar's content becomes, at most, a **proposal**.
   - It reaches the georeferencing in use only through the existing path: `georeference.validate`, then the person's review, a mandatory reason and an explicit confirmation, then `georeference.apply`.
   - That path writes an append-only `georef_assertion`, labelled as recorded (user-asserted) georeferencing.
3. **What a sidecar is read from:** its stored bytes, every time it is reviewed or used, after the SHA-256 recorded at upload is checked. A stored sidecar that no longer matches its checksum is refused (`SIDECAR_CHANGED`) and never used.
4. **A world file is not a CRS.**
   - A world file alone never places a raster. The review says why, and the raster stays unplaced.
   - The only exception is a CRS that is already usable: one the raster file declares, or one recorded earlier. In that case the world file supplies only the coordinates, exactly as when a geotransform is entered by hand.
   - A `.prj` alone cannot place a raster that has no geotransform.
5. **A CRS is accepted only when the existing CRS machinery resolves it** (`resolve_wkt`). All of these must hold:
   - the WKT parses with PROJ;
   - **exactly one** registry entry matches it at 100 % confidence;
   - that code resolves through `geo.crs.resolve`;
   - the registry CRS is equivalent to the definition, axis order aside, because rasters are always read in x/y order;
   - it is a 2D geographic or projected CRS.

   A name is never enough. A definition called "WGS 84 / UTM zone 43N" with a different central meridian matches at only 25 % and is refused. The change then names the registry code, and `validate` checks it as it would any chosen CRS.
6. **Valid raster-owned metadata is never replaced.**
   - If the file's own georeferencing is valid, sidecars are shown for comparison only. Any disagreement is listed, and recording is refused (`ALREADY_GEOREFERENCED`).
   - If the file has a usable geotransform but no CRS, a world file that disagrees with it is shown as a conflict. The file's geotransform is kept, and only the CRS can be proposed.
7. **Contradictions are explicit.**
   - When two current sidecars disagree about the geotransform (world file versus `.aux.xml`) or the CRS (`.prj` versus `.aux.xml`), nothing is proposed. The state is `conflict`.
   - The person chooses which file to review. The other stays recorded and unused.
8. **Unknown content is reported, not imported.** These are listed as "present, not used":
   - `.aux.xml` elements that are not understood;
   - dataset metadata domains;
   - RPC models;
   - GCP lists;
   - band elements such as histograms.

   Nothing is fitted from GCPs or RPCs.
9. **Nothing is invented.** No coordinate, control point, CRS or extent is derived from names, folders, image content or other files. If what the sidecars give is insufficient, the raster stays unplaced and the review says what is missing.

## 3. Pairing

- **With an upload**, on the raster tab of Upload & Ingestion:
  - Sidecar files can be chosen together with their raster, in any order. A sidecar waits until the rasters added before it are stored.
  - It pairs with a raster in the same batch whose name matches. Otherwise the server pairs it with the one raster in the project whose name matches.
- **Matching rules** (case-insensitive, as on Windows):
  - `scan.tfw`, `scan.tifw`, `scan.tiffw`, `scan.wld`, `scan.prj` and `scan.aux.xml` match `scan.tif` or `scan.tiff`;
  - `scan.tif.aux.xml` matches exactly `scan.tif`.
- **Refused, nothing stored, audited as `sidecar.upload_rejected`:**
  - no matching raster: `SIDECAR_NO_MATCHING_RASTER`, 422;
  - several matching rasters: `SIDECAR_PAIRING_AMBIGUOUS`, 409;
  - an unsupported name (415);
  - an empty file (422);
  - a file over the limit (413);
  - a target that is not a raster (409).
- **Attached by hand** from the raster's page: allowed whatever the names. The sidecar is recorded with `pairing = explicit` and shown as "Attached by hand; names differ".
- **Identical content** added twice for the same raster is not stored again (HTTP 200, `duplicate: true`).
- **The latest sidecar of each kind is the current one.** Older ones are kept and shown as superseded and unused.
- **Size limits:** world file 64 KB, `.prj` 64 KB, `.aux.xml` 4 MB. Text must be UTF-8 or ASCII (a BOM is allowed).

## 4. Parsing and validation (`geopoc/geo/sidecars.py`, parser version `sidecars-v1`)

| Sidecar | Reading | Validation | Result states |
| --- | --- | --- | --- |
| World file | Exactly six non-empty lines, each a plain decimal number (commas, NaN, infinity and other notations refused). Converted with the existing `georeference.parse_world_file`: the pixel centre becomes GDAL's top-left corner. | `geotransform_problem` (identity, degenerate, zero pixel size). Rotation is reported, and the full affine transform is used. | parsed, or malformed |
| `.prj` | WKT only. PROJ strings and bare codes are refused as unsupported. | `resolve_wkt`, as in rule 5. The nearest registry matches and their confidence are shown when there is no full match. | parsed (CRS resolved or not), malformed, unsupported |
| `.aux.xml` | Python's ElementTree (Expat 2.6.0) after refusing any `<!DOCTYPE` or `<!ENTITY`; GDAL PAM files never contain either. The root must be `<PAMDataset>`. | `<SRS>`: `resolve_wkt`; a `dataAxisToSRSAxisMapping` other than x/y order is refused. `<GeoTransform>`: six numbers, `geotransform_problem`. `<PAMRasterBand band="n">`: the band must exist; `Description`, `NoDataValue` (must be representable in the band's type), `Scale`, `Offset`, `UnitType`, `ColorInterp`. Everything else: present, not used. | parsed, malformed, unsupported (no understood fields) |

The parsers are pure functions: no file-system access and no network.

### Review states (`sidecars.review`)

| State | Meaning |
| --- | --- |
| `none` | No sidecar has been added. |
| `file_valid` | The raster's own georeferencing is valid. Sidecars are compared only; nothing is offered. |
| `proposal` | A change can be reviewed: an operation, the values, and which file each value comes from. |
| `conflict` | Two current sidecars disagree; a choice is needed. |
| `insufficient` | What the sidecars give cannot place the raster. The reasons, and each file's problems, are listed. |
| `in_use` | The sidecar values are already the ones in use. |

Each sidecar is compared field by field with what the raster file declares and with what is in use. The relation is one of `same`, `differs`, `none` (nothing to compare with) or `unresolved` (a CRS definition that did not resolve).

## 5. Recording a change

- **Endpoints:**

  | Endpoint | Purpose |
  | --- | --- |
  | `POST /api/v1/projects/{id}/sidecars?filename=…[&asset_id=…]` | Upload; pairs by name, or uses the raster given. |
  | `POST /api/v1/assets/{id}/sidecars?filename=…` | Attach to this raster by hand. |
  | `GET /api/v1/assets/{id}/sidecars` | The review. |
  | `POST /api/v1/assets/{id}/sidecars/validate` | The full georeferencing checks. Stores nothing. |
  | `POST /api/v1/assets/{id}/sidecars/apply` | Record the change. |

- **Before anything is recorded, `apply` requires:**
  - `confirm: true`;
  - a non-blank reason;
  - the georeferencing version that was reviewed (optimistic concurrency; otherwise `GEOREFERENCE_VERSION_CONFLICT`);
  - the exact set of sidecar ids that was reviewed (`SIDECARS_CHANGED` if a file was added in the meantime);
  - unchanged sidecar bytes;
  - an unchanged original (the existing `ORIGINAL_UNCHANGED` check);
  - every validation check passing, including the tile render from the original.
- **What is recorded:** a `georef_assertion` with method `sidecar_geotransform` (operation `set_geotransform`) or `sidecar_crs` (operation `assign_crs`). Migration 0007 extends the method constraint. The assertion also holds:
  - for a world file, its full text (`world_file`);
  - a `geotransform_source` naming the file, its checksum and who added it;
  - `validation.sidecars`, listing each file used with its id, name, kind, SHA-256, uploader, time and role (geotransform or CRS);
  - the sidecar checks (`SIDECAR_UNCHANGED`, `SIDECAR_CONFLICT` and `SIDECAR_PROBLEM` warnings) alongside the usual ones.
- **Audit:** `sidecar.upload`, `sidecar.upload_rejected`, `georeference.set_geotransform` / `georeference.assign_crs` (with the method and sidecar ids), and `georeference.refused`.
- **Tiles:** the new version changes the tile URL (`?v=n`) and the server cache key (version and assertion id), so a tile drawn with earlier metadata is never reused. This is tested by moving the raster in a later version and checking the same tile address.
- **Table `sidecar`** (append-only by trigger):
  - id, project, raster, kind, original file name, blob SHA-256, size;
  - pairing (`filename_match` / `explicit`);
  - status at upload, parser version, uploader, time, request id.

  Parsed values are not stored. They are always re-read from the verified bytes, so a review always shows what the file says.

## 6. Interface (existing design system, no visual changes elsewhere)

- **Upload & Ingestion, raster tab:**
  - accepts the sidecar extensions and says that sidecars are kept for review and never applied on their own;
  - a sidecar row reads "Kept for review with «raster» (matched by name). Nothing has been applied.", with what the file is, any problems, and a link to review it;
  - an orphan sidecar row reads "Not accepted", with the reason.
- **Asset page, "Sidecar files" panel** under Spatial placement:
  - the review state and its explanation;
  - disagreements with the raster file;
  - each file: name, kind, read status, superseded or attached by hand, summary, role, problems, "Present in the file, not used", a comparison table, size, checksum and uploader;
  - a file input to attach a sidecar by hand;
  - when files disagree, a choice of which file to review;
  - the proposed change, a required reason, "Check this change" (all checks, plus where the corners would be), the confirmation checkbox, then "Record as version n".
- **Spatial placement history:** the method reads "Geotransform from a sidecar file, reviewed and confirmed" or "CRS from a sidecar file, reviewed and confirmed", and lists the sidecar files used with their checksums.

## 7. Tests

All tests use synthetic data. Expected coordinates are worked out in each test from the world-file convention and the affine formula, then transformed with PyProj. **No real-world imagery or real supplier sidecars have been tested** (§8).

| Where | What |
| --- | --- |
| `services/api/tests/test_sidecars.py` (52 tests, incl. parametrised) | Full workflow (upload → review → mandatory reason → validate → confirm → record → placed, user-asserted, tile drawn, audit, original and sidecar bytes unchanged); world file alone; world file with a CRS already usable; `.prj` alone; `.prj` assigning a CRS to the file's coordinates; valid raster metadata never overridden, with conflicts shown; a disagreeing world file shown while the file's geotransform is kept; contradictory sidecars needing a choice, and an invalid choice refused; superseded sidecars; 12 malformed or unsupported cases (5 lines, decimal comma, NaN, degenerate, binary, non-WKT, PROJ string, blank, broken XML, wrong root, XXE entity, bad GeoTransform); unresolved CRS definitions (custom LCC, same-name UTM with a wrong meridian, vertical, geocentric, compound); `.prj` dialects (ESRI WKT1, GDAL WKT1, WKT2 for EPSG:32643, 4326, 27700, 3857); a rotated world file with independently computed corners; `.aux.xml` band metadata shown and not applied, and unknown content listed (RPC, metadata domain, GCPs, unknown elements, histograms, a band out of range, nodata not representable); the axis-mapping rule; pairing (case, `.tif.aux.xml`, orphan, explicit, duplicate, unsupported, empty, too large, ambiguous, not a raster); ingestion rules; concurrent edits (`GEOREFERENCE_VERSION_CONFLICT`) and a sidecar added after review (`SIDECARS_CHANGED`); a changed stored sidecar; a changed original; append-only rows; tile-cache replacement on a new version; no PAM files written; naming rules. |
| `apps/web/src/components/sidecars.test.tsx` (8 tests) | The panel's states, the reason, check and confirmation sequence, the choice between disagreeing files, conflicts with valid metadata, problems and unused content; front-end name pairing and size checks. |
| `apps/web/e2e/phase3c.spec.ts` (2 tests, real API, database and worker) | A world file and `.prj` uploaded with a raster, reviewed, confirmed and recorded; the raster placed on the map and its tiles served; original unchanged; no external requests. A world file alone (not placed), valid raster metadata with a disagreeing world file (not overridden), and an orphan `.prj` (refused). |
| `apps/web/review/phase3c.spec.ts` | Screenshot walkthrough (eight screenshots; not committed). |

## 8. Limitations and remaining gaps

- **Synthetic data only.**
  - Real supplier world files, ArcGIS `.prj` files and GDAL-written `.aux.xml` files have not been tested.
  - Encodings other than UTF-8 or ASCII (for example a Windows-1252 `.prj` with accented names) are refused rather than guessed.
- **Conservative CRS matching refuses some valid definitions.**
  - For example, NZGD2000 / New Zealand Transverse Mercator written as ESRI WKT1 is not equivalent to EPSG:2193 when PROJ compares the two, and is refused.
  - A person can still choose the CRS from the registry in Spatial placement, with a documented reason.
- **Band metadata from `.aux.xml` is not applied.** Description, nodata, scale/offset, units and colour interpretation are compared and shown "shown only, not applied". Applying them would change statistics, pixel values and display in the five raster tools, and would need its own append-only record and review. It is not built.
- **Not used:** GCPs and RPCs (no orthorectification), external overviews (`.ovr`), masks (`.msk`), `.vrt` files and `.aux` (old Imagine) files.
- **Sidecars cannot be removed**; rows are append-only. A wrong file is simply not confirmed, or is superseded by a later upload of the same kind.
- **Exports** (CSV, GeoJSON, JSON) do not yet list the sidecar files behind a recorded version. The georeferencing history and the audit log do.
- **Pairing by name** looks only at rasters in the same project. A sidecar for a raster in another project must be uploaded there.
- The downgrade of migration 0007 is refused once any version was recorded from sidecars, so history is never lost.

## 9. Differences from the earlier plan

| Earlier plan | Implemented | Why |
| --- | --- | --- |
| `.prj`: when the EPSG match is uncertain, keep the WKT as given | Only CRSs the registry resolves completely are accepted; others are refused with the nearest matches shown | The owner's follow-up requires accepting only definitions the existing CRS machinery can resolve and validate. |
| defusedxml for `.aux.xml` | Standard-library ElementTree (Expat 2.6.0), refusing DOCTYPE/ENTITY before parsing | No new dependency is needed. ElementTree does not fetch external entities, Expat 2.6.0 limits entity expansion, and PAM files never declare a DTD. |
| Limits of 1 KB / 1 MB / 1 MB | 64 KB / 64 KB / 4 MB | Real `.aux.xml` files with histograms for many bands can exceed 1 MB; the limits still bound memory. |
| Parsed values stored in the `sidecar` row | Re-parsed from the verified bytes each time | A single source of truth: what is shown is what the file says. |
| Confirmed band metadata in a `band_assertion` table | Not built; band metadata is shown only | It would change the accepted raster tools' behaviour (see §8). |
| Pasting sidecar text on the raster page | Files only; the existing world-file paste in Spatial placement remains | Keeps every sidecar a stored, checksummed file. |
| `geotransform_source = "world_file:<id>"` | A readable source plus `validation.sidecars` with ids and checksums | Readable in the history, and complete for audit. |
