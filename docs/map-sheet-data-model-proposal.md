# Map-sheet data model: proposal (not applied)

**Status: proposal only.** No migration has been written into `services/api/migrations`, none has been run, and the working
database is still at revision 0009. Nothing here changes until it is separately approved.

## What the existing model can and cannot represent

| Need | Existing model | Verdict |
|---|---|---|
| Store the original, unmodified, once | `blob` + `asset` | Sufficient. |
| A georeferenced GeoTIFF sheet | `asset` (modality `overhead_raster`) + `raster_metadata` | Sufficient. |
| A scanned TIFF/PNG/JPEG sheet | same; `raster_metadata.crs_status = 'missing'`, `georeferenced = false` | Sufficient. |
| Sheet attributes (series, label, scale, edition) | `asset.metadata` JSONB, written at upload | Works, but **immutable and unstructured**: cannot be corrected, listed by series, or kept unique per project. |
| Georeferencing recorded by a person | `georef_assertion` (append-only) | Sufficient for CRS + geotransform. |
| Control points as evidence | `georef_assertion.validation` JSONB; method `geotransform_entered` | Works, but the method name does not say "control points", the points cannot be queried, and `ck_georef_assertion_method` allows only four methods. |
| A PDF sheet | `asset` with modality `overhead_raster` and **no** `raster_metadata` row | **Insufficient.** `ck_asset_modality` allows only `ground_image`, `overhead_raster`, `video`. A PDF is not a raster; every raster code path must remember to tolerate a missing raster row. |
| Unresolved metadata recorded by a person | `audit_event` (append-only) | Stopgap: not a structured record, not queryable by field or status, mixed with unrelated events. |
| A custom CRS (for an ESM definition once an authority supplies it) | `crs_definition` (empty; decisions U-006 / U-007 open) | The right home for an approved definition. Not used or filled here. |
| Relationships between sheets (index, adjacency, edition supersession) | none | **Insufficient** if ever needed. Not required now. |

So the existing model is adequate for raster sheets and for the georeferencing workflows, and inadequate in three places:
PDF sheets, correctable sheet attributes, and structured unresolved-metadata notes.

## Proposed changes

Smallest set first; each is independent.

### P1. Allow a map document modality (PDF)

```sql
ALTER TABLE asset DROP CONSTRAINT ck_asset_modality;
ALTER TABLE asset ADD CONSTRAINT ck_asset_modality
  CHECK (modality IN ('ground_image', 'overhead_raster', 'video', 'map_document'));
```

PDF sheets become `map_document` assets, with no `raster_metadata`. Code that handles `overhead_raster` is then never reached
by a PDF. Alternative: leave PDFs as `overhead_raster` (current behaviour) and keep guarding for a missing raster row.

### P2. Append-only sheet declaration

```sql
CREATE TABLE map_sheet_declaration (
  id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES asset(id),
  version_no integer NOT NULL CHECK (version_no >= 1),
  series text NOT NULL CHECK (series IN ('osm', 'esm', 'unspecified')),
  sheet_label text, scale_denominator integer CHECK (scale_denominator > 0), edition text,
  reason text, actor_id uuid NOT NULL REFERENCES workspace_user(id), actor_label text NOT NULL,
  created_at timestamptz NOT NULL, request_id text,
  UNIQUE (asset_id, version_no)
);
-- plus the same append-only trigger as georef_assertion
```

The latest version is current. The upload would write version 1 (replacing `asset.metadata["map_sheet"].declaration`, which
stays for old rows). Makes a mistaken series or label correctable with history and allows listing by series.

### P3. Structured unresolved-metadata notes

```sql
CREATE TABLE map_sheet_metadata_note (
  id uuid PRIMARY KEY, asset_id uuid NOT NULL REFERENCES asset(id),
  field text NOT NULL CHECK (field IN ('datum','ellipsoid','prime_meridian','projection','projection_parameters',
                                       'units','coordinate_grid','extent','transformation_to_wgs84','other')),
  value text NOT NULL, source text NOT NULL, status text NOT NULL CHECK (status IN ('unverified','needs_authority')),
  actor_id uuid NOT NULL REFERENCES workspace_user(id), actor_label text NOT NULL,
  created_at timestamptz NOT NULL, request_id text
);
```

Append-only. Replaces the use of `audit_event`. Existing notes would be copied in by a data migration.

### P4. A control-point georeferencing method and rows

```sql
ALTER TABLE georef_assertion DROP CONSTRAINT ck_georef_assertion_method;
ALTER TABLE georef_assertion ADD CONSTRAINT ck_georef_assertion_method
  CHECK (method IN ('registry_crs_selected','geotransform_entered','world_file_entered','file_metadata_restored',
                    <the sidecar methods added by 0007>, 'control_points'));

CREATE TABLE georef_control_point (
  assertion_id uuid NOT NULL REFERENCES georef_assertion(id), ordinal integer NOT NULL,
  label text NOT NULL, pixel_column double precision NOT NULL, pixel_row double precision NOT NULL,
  x double precision NOT NULL, y double precision NOT NULL,
  residual_pixels double precision NOT NULL, residual_map_units double precision NOT NULL,
  PRIMARY KEY (assertion_id, ordinal)
);
```

Append-only, written in the same transaction as the assertion. `validation.control_points` stays for the fit summary.

### P5. Persist the datum-transformation choice for file-georeferenced rasters (from Phase 1)

A small append-only `raster_transformation_choice (asset_id, operation_id, actor, reason, created_at)`. Separate from the
map-sheet work but closes a Phase 1 limitation.

### Not proposed

- A `map_sheet` entity duplicating `asset`. A sheet is an asset with a declaration; a second identity invites drift.
- Sheet indexes, adjacency or mosaics. Wait for real series data and a requirement.
- Defining any ESM CRS. An authoritative definition belongs in `crs_definition`, added only when one is supplied and approved.

## Migration plan (if approved)

1. One revision `0010`, or one per proposal so each can be reverted alone. P1 and P4 only alter CHECK constraints (fast, no
   table rewrite); P2, P3, P5 add tables.
2. Backwards compatible: no column is dropped; the application reads old rows (metadata JSON, audit notes) when the new tables
   have none.
3. Test: the migration chain tests, a downgrade test, a data-copy test for notes, and the existing suites. Run first against
   scratch databases (`geopoc_test`, `geopoc_e2e`), then a **backup of the working database** before it is applied there.
4. Rollback: drop the new tables and restore the previous CHECK lists (data in the new tables would be lost, so export first).

## Risks

- Wider CHECKs are harmless to old rows; narrowing them back after use would fail while new values exist.
- P1 moves the PDF off `overhead_raster`; existing PDF assets (if any) need a one-line data update.
- The roles / credentials work (G6-G11) is separate and unapproved; this proposal does not depend on it.

## Decision requested

Approve any of P1-P5 individually, or none. Until then the current stopgaps work and are documented in
[map-sheets.md](map-sheets.md).
