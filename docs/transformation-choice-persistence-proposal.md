# Persisting datum-transformation choices: proposal (not applied)

**Status: proposal only.** No migration was written or run; the working database is still at revision 0009. Nothing here
changes until it is separately approved.

**Extended by the accuracy gate (Phase 2).** The same gap now has a second consequence: a file-georeferenced raster whose only
datum shift is worse than 10 m (or has no stated accuracy) needs an *acknowledgement*, which has nowhere to be stored without a
schema change, so such a raster is blocked ([transformation-acknowledgement.md](transformation-acknowledgement.md)). A table
like the one proposed below, carrying an acknowledgement beside the choice, would lift that block. Not applied.

## Today

| Where the choice lives | Stored? |
|---|---|
| Raster whose georeferencing a person recorded | **Yes**, inside that version: `georef_assertion.validation.transformation.operation.id`. Append-only, read back by every conversion path (`convert.recorded_operation`). |
| Raster using its own file georeferencing | **No.** Given per request (`?operation=`, `operation=assetId:opId`) and held in the browser view. |
| Project area of interest | **Yes**, as provenance in `project.aoi_source.transformation` (operation id, class, accuracy, PROJ version) at the time the area was entered. |
| Point conversion, pixel lookup, measurement, 3D position | Per request only. |

So the gap is the second row: a person who chooses a datum shift for a file-georeferenced raster has to choose again after a
reload, and nothing records what was chosen, by whom, or when.

## What needs to be stored

For one raster (or one project), one decision:

- `asset_id` (and for a project-level decision, `project_id`), a per-subject `choice_no`;
- `operation_id` and, as a snapshot, its `name`, `authority_code`, `classification`, `accuracy_m` and area of use;
- the **candidates that were offered** (ids, classes, accuracies) and the plan status, so a reader can see what was chosen among;
- the PROJ and pyproj versions and a SHA-256 of the operation's pipeline definition (reproducibility, below);
- the actor, reason, `created_at`, `request_id`; and a `cleared` row type (back to "no choice").

## Can the existing append-only georeferencing assertions be reused?

Partly, and not recommended for file-georeferenced rasters.

- **For recorded georeferencing: already reused.** The choice rides in the assertion's `validation`, and that is kept.
- **For file-georeferenced rasters: poor fit.** `georef_assertion` records a CRS and a geotransform, requires `georeferenced`
  to differ from the file's own, and `apply` refuses a raster whose own georeferencing is valid (`ALREADY_GEOREFERENCED`). A
  new `choose_transformation` operation would need that rule relaxed, copy CRS and geotransform unchanged into a new version,
  widen `ck_georef_assertion_operation`, and bump `georef_version`, which is also the tile cache version. That conflates
  "where the pixels are" with "which datum shift carries them to WGS 84", and a datum choice would look like a change of
  georeferencing in the history.

## Recommended: one small append-only table

```sql
CREATE TABLE transformation_choice (
  id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES asset(id),
  choice_no integer NOT NULL CHECK (choice_no >= 1),
  kind text NOT NULL CHECK (kind IN ('chosen', 'cleared')),
  operation_id text,                       -- NULL when cleared
  operation jsonb,                         -- name, authority code, class, accuracy, area of use (snapshot)
  candidates jsonb NOT NULL,               -- what was on offer, with plan status
  target_crs text NOT NULL DEFAULT 'EPSG:4326',
  proj_version text NOT NULL, pyproj_version text NOT NULL, pipeline_sha256 text,
  reason text NOT NULL CHECK (length(btrim(reason)) > 0),
  actor_id uuid NOT NULL REFERENCES workspace_user(id), actor_label text NOT NULL,
  created_at timestamptz NOT NULL, request_id text,
  UNIQUE (asset_id, choice_no)
);
-- the same append-only trigger as georef_assertion
```

Rules:

1. **Precedence.** If the raster's georeferencing was recorded by a person, that version's own choice stands and a conflicting
   row is refused (change the georeferencing instead). Otherwise the latest `chosen` row is used; `cleared` means none.
2. **Optimistic concurrency** by `expected_choice_no`, as georeferencing does.
3. **A choice must validate**: it must be one of the usable operations for the raster's footprint now. A ballpark or
   missing-grid operation can never be stored.
4. **Request-level `operation=` stays** as an explicit "preview this other shift" override that is not stored.
5. **AOI** keeps its provenance in `aoi_source`; no new table is needed for it.

## Historical choices are retained

Rows are never updated or deleted (trigger). A new choice or a clear is a new row; the history endpoint lists every row with
its candidates, actor and reason. Choices inside recorded-georeferencing versions stay readable where they are. Exports and
reports can cite `choice_no`, so a figure can be traced to the decision behind it.

## Effect on tile caching and reproducibility

- **Caching.** The tile cache key already contains `op=<operation id>` and the georeferencing version, and the layer list
  already puts the chosen id in the tile URL. A stored choice would add its `choice_no` to the URL (`&tx=3`). A new choice is a
  new URL, so a browser or server never reuses a tile drawn with another shift, and no cache needs purging.
- **Reproducibility.** An operation id such as `EPSG:1812` names a registry entry, not a fixed pipeline: a different PROJ
  database release can change its definition or accuracy. The row therefore keeps the PROJ version and the pipeline hash. When
  the running PROJ differs, the application should say so ("chosen with PROJ 9.5.1; now 9.6.0, pipeline changed") and ask the
  person to confirm again rather than apply the choice silently.
- **Already-stored footprints** are not rewritten. The layer list evaluates them against the current choice, as it does now.

## Migration plan (if approved)

One revision (`0010` or later, after any roles work), one new table and trigger, no existing table altered, so it is
backwards compatible and reversible by dropping the table (export the rows first). Test on `geopoc_test` and `geopoc_e2e`
first, with the migration-chain tests, a downgrade test and a precedence test; back up the working database before applying.

## Risks

- A stored choice can hide that the registry's candidates changed; the stored candidate snapshot and the version check exist
  for this.
- A project-wide default ("always use X for this CRS") is deliberately not proposed: shifts depend on the area, and a default
  would be a silent selection again.

## Decision requested

Approve the table, approve it with changes, or leave choices per request as now. Until then, record a georeferencing version
for a raster that needs a stored choice (its choice is kept in the version's history).
