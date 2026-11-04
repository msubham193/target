# Map annotations: implemented in code, migration NOT applied to the working database

Status: the code, the migration file and the tests exist. The migration (`0009_map_annotation.py`) has been applied only to
scratch databases (`geopoc_test`, `geopoc_migration_ann`). The working database `geopoc` is still at revision 0008 and has no
`map_annotation` table. Applying it needs a separate approval (see "Applying it" below).

## Decisions this follows

1. Annotations are independent, project-owned records. No column refers to a photograph, an observation, a raster or a job.
2. Points, lines and polygons (one outline, no holes), a user-written note, editing, viewing and soft deletion.
3. Line length and polygon area (and outline length) are shown when valid, with explicit units and stated limitations.
4. History is kept and every change is audited. Deleted annotations are left out of normal views by default.
5. Annotations are kept apart from AI observations and confirmed photograph locations: own table, own endpoints,
   `feature_type: "user_annotation"`, not in any observation list, observation count or CSV row. They do have their own
   section in the findings report and their own members in the JSON and GeoJSON exports (see "In the findings report and
   the exports").
6. Every operation is scoped to the project in the URL. Geometry and coordinate system are validated.
7. No identity is invented. The author is the local profile that made the change (attribution, not authentication).

## Migration numbering

Existing history: `0001` ... `0008`, one straight line (`0008_photo_location` is the head). No `0009` existed in the
repository, and the audit-protection migration (Phase 0 item 5) has not been written. The annotation migration is therefore
`0009` with `down_revision = "0008"`.

It does not depend on the audit-protection migration and that one does not depend on it (audit protection concerns
`audit_event` and a database role; annotations add one table and touch no existing table). Whichever is applied second takes
the next number: if audit protection is approved first, rename the annotation file to `0010_map_annotation.py`, set
`revision = "0010"`, `down_revision = "0009"`, and nothing else changes. `tests/test_migration_chain.py` fails if two
migrations claim the same number or parent, or if the numbers are not contiguous, so a clash cannot be applied by accident.

The migration file is in `services/api/migrations/versions/`, which means `alembic upgrade head`, `scripts\migrate.ps1` and the
test fixtures all see it. Nothing applies it on its own: the application never migrates at start-up, `scripts\dev.ps1` only
reports "database behind code", and `scripts\migrate.ps1 -Apply` needs a verified backup. Until it is applied, the annotation
endpoints answer `503 ANNOTATIONS_NOT_MIGRATED` and the panel says so; every other feature is unaffected.

## Schema (`map_annotation`, append-only)

```text
id             uuid PK            one row per version
annotation_id  uuid NOT NULL      stable across versions
project_id     uuid NOT NULL FK project.id
version_no     integer NOT NULL   UNIQUE (annotation_id, version_no)
action         text   create | update | delete
status         text   active | deleted
kind           text   point | line | polygon
geom           geometry(Geometry, 4326) NOT NULL
crs_id         text   = 'EPSG:4326'
note           text   <= 2000 characters (may be empty), shown as plain text
reason         text   <= 1000 characters, optional
actor_id       uuid FK workspace_user.id, actor_label text NOT NULL
created_at     timestamptz, request_id text
```

Constraints (all in the database, so a bug in the service cannot store an invalid shape): geometry non-empty, valid, two
dimensional, of the type its `kind` says, a polygon with no holes, coordinates within longitude/latitude range;
`action`/`version_no`/`status` consistent (version 1 is a `create`, a `delete` is a later `deleted` version); the CRS is
EPSG:4326. A trigger refuses every UPDATE and DELETE (the existing `append_only()` function). Indexes: `(project_id,
annotation_id, version_no)` and GiST on `geom`. Downgrade is refused while any annotation exists.

Foreign keys: `project` and `workspace_user` only.

## API (all under `/api/v1/projects/{project_id}/annotations`)

| Operation | Notes |
|---|---|
| `GET` | current annotations; deleted ones only with `?include_deleted=true`; counts of active and deleted |
| `POST` | `{kind, geometry (GeoJSON), crs_id, note}`; profile required; audit `annotation.create` |
| `GET /{annotation_id}` | one annotation, including a deleted one (its status says so) |
| `PATCH /{annotation_id}` | `{expected_version, geometry?, note?, reason?}`; a stale version is a 409; audit `annotation.update` |
| `DELETE /{annotation_id}?expected_version=N` | appends a `deleted` version; audit `annotation.delete` |
| `GET /{annotation_id}/history` | every version, oldest first |

An annotation id from another project is `404 ANNOTATION_NOT_FOUND`. A refused shape (422) is audited as
`annotation.refused` with the reason code and not stored. Notes and reasons are not copied into the audit log (they are in the
history).

Validation: CRS must be EPSG:4326 (anything else is refused, never converted or guessed); positions are
`[longitude, latitude]` with no elevation; finite numbers within range; a position of exactly 0, 0 is refused as a missing
value; at most 1000 vertices; a line needs two different positions; a polygon outline must be closed, simple (no
self-intersection) and enclose an area, with no holes; shapes crossing the 180° meridian are refused; multi-part shapes and
collections are refused; the kind of an annotation cannot change; coordinates are rounded to 8 decimals and returned as
stored.

## Measurement

Computed on read from the stored vertices, never stored. Geodesic on the WGS 84 ellipsoid (PROJ/Karney, the same routine as
the map's measurement tool). Fields: `length_m` (lines), `area_m2` and `perimeter_m` (polygons). Each response carries the
method and these limitations: computed from the vertices as drawn, so as accurate as the drawing and not a survey
measurement; the map draws edges as straight lines on screen while the figure is geodesic, which differs on long edges; a
polygon's area is the area of its outline only. A point and a deleted annotation have no measurement.

## User interface

On the Map page, the Annotations panel (right column): Add point / line / polygon, click the map to place positions (a
point can also be typed as coordinates, as a keyboard alternative), a note box, Save; a list with Show, Edit, Delete (with
confirmation) and History; "Show on the map" and "List deleted annotations" switches. Annotations are drawn in magenta with
the legend "Your annotations (not findings)", apart from the green photograph markers and the blue measurement sketch.
Deleted annotations are never drawn.

## In the findings report and the exports

Added after the first version; it reads the existing `map_annotation` table and changes no schema. One read of the project's
annotations (`geopoc/annotation_export.py`) feeds all three, and every query is limited to the project.

| Output | Where the annotations are | Deleted annotations |
|---|---|---|
| Findings report (`GET /api/v1/projects/{id}/report`, format version 1.1) | `annotations` (`available`, `note`, `active`, `deleted`, `items`); page section "9. Map annotations"; summary counts `annotations_active`, `annotations_deleted` | Listed in `items` with `status: "deleted"`, who deleted them and when, their last shape, and every version; shown under their own heading marked "Deleted: not an active mark" |
| `exports/observations.json` | `annotations` (same fields as the report, with each version's shape, note, reason, author and time) | In `items` with `status: "deleted"` |
| `exports/observations.geojson` | Features with `properties.feature_type: "user_annotation"` and `id: "annotation:<id>"` (`annotation_id`, `kind`, `status`, `note`, `version_no`, `crs_id`, `created_by/at`, `updated_by/at`); the `annotations` member says how many were exported | **Not written as features** (a GIS would draw them like any other feature). Counted in `annotations.deleted_not_exported`; the JSON export and the report list them |
| `exports/observations.csv` | Nothing. One row per observation, exactly as before | Not applicable |

- Each annotation carries its ID, geometry (GeoJSON, EPSG:4326, [longitude, latitude]), kind, note, version, author and
  timestamps. The author is a local profile: attribution, not authentication. The report and the JSON export add length or
  area for active lines and polygons, labelled approximate, and none for a point or a deleted annotation.
- The `status` filter of the exports applies to observations only; annotations are not filtered by it.
- Annotations are not counted among findings, photographs or locations, and the observation parts of the report and
  exports are identical with and without annotations (tested).
- Reading never writes to `map_annotation`. History stays append-only; nothing here updates or deletes a version.
- Same access rules as before: the report and the exports need a local profile and each is recorded in the audit log. The
  JSON and GeoJSON export records and the report record add the annotation counts (never note text); the CSV record is
  unchanged. Another project's annotations never appear.
- A database that has not been migrated for annotations still produces the report and both exports: `available` is
  `false`, `active` and `deleted` are `null` (unknown, not zero) and the note says "that does not mean the project has none".
- Report limit: a long shape lists its first 12 positions; all positions are in the JSON and GeoJSON exports.

## Applying it (needs separate approval)

1. Decide the number: `0009`, or `0010` if audit protection is applied first (rename as above).
2. `.\scripts\backup-db.ps1 -Verify` (a verified backup of 24 hours or less is required).
3. `.\scripts\migrate.ps1` (dry run: shows the current revision, the target and the backup check).
4. `.\scripts\migrate.ps1 -Apply`.
5. Restart the API so the new routes are served (the running API is an older build).

The change adds one table and its indexes and trigger. It changes no existing table or row. It is reversible while the table
is empty (`alembic downgrade 0008`); once annotations exist the downgrade is refused so they cannot be lost.

## Not done in this version

- No separate annotations-only export file (they are in the JSON and GeoJSON exports and the report).
- No dragging of vertices on the map: an edit changes the note, or redraws the whole shape.
- No restore of a deleted annotation (the history keeps everything, so a restore can be added without a schema change).
- Not shown in the Photo Workspace map tab; only on the Map page.
- No holes in polygons, no multi-part shapes, no shapes across the 180° meridian.
- A database check cannot confirm that all versions of one annotation share a project and kind; the service enforces it and
  the tests cover it.
