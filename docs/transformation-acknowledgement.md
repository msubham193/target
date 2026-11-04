# Transformation accuracy and acknowledgement

CRS correctness hardening, Phase 2. It adds **no database migration and changes no schema**: the working database stays at
revision 0009. The policy lives in one place, `geopoc/geo/transform.py` (`ACK_ACCURACY_M = 10.0`), and every conversion that
moves positions between reference systems goes through it (see [crs-conversion-coverage.md](crs-conversion-coverage.md)).

## The policy

| The operation PROJ would use | Used? |
|---|---|
| Exact (no datum change) | Yes, without acknowledgement. |
| Approximate, documented accuracy **10 m or better** (exactly 10 m included) | Yes, without acknowledgement. If several apply, a person still has to choose one. |
| Approximate, documented accuracy **worse than 10 m** | **No**, until a person has acknowledged that specific operation (`TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED`). |
| Approximate, **no stated accuracy** (a registered shift, not a ballpark) | **No**, until acknowledged. It is never used automatically and never counted as "good enough". |
| Several operations apply | A person must choose one; nothing is preselected. The suggestion (smallest documented accuracy) is only a suggestion. The chosen one is then subject to the rows above. |
| Ballpark | **Always blocked.** An acknowledgement does not unlock it. Only the existing explicit `accept_ballpark` on the point calculator does, and marks the result `BALLPARK_ACCEPTED`. Nothing falls back to it. |
| Needs a grid that is not installed | Blocked. An acknowledgement does not unlock it. |

The 10 m figure was decided by the project owner. The accuracy is the figure published with the operation in the local
PROJ/EPSG registry. It describes the datum shift only, not the map sheet, the image, a hand-entered georeferencing or the choice
of CRS.

In the local registry (3,962 non-deprecated transformations) 870 state no accuracy and 251 state more than 10 m, so these
cases are not rare. Kalianpur 1962 / UTM 43N (EPSG:24313) is the usual example: its only registered shift (EPSG:1247) is
documented to 999 m, and it used to be applied without a word.

### Ballpark is recognised by structure

`has_ballpark_transformation` on the operation's steps (a PROJ property) is the primary signal. The word "ballpark" in a name is
corroboration only. If the two disagree the operation is still treated as a ballpark and the disagreement is logged. An
operation with no stated accuracy that PROJ does not call a ballpark is **not** a ballpark: it is approximate with an unknown
error, which needs acknowledgement.

## What an acknowledgement is

A statement by a person that they accept one specific operation despite its accuracy. It matches only if **all** of these are
the same as when it was given:

- the operation id (for example `EPSG:1247`),
- the source CRS,
- the datum of the target (an acknowledgement of a shift to WGS 84 also covers the Web Mercator tiles drawn from it, because
  the datum shift is the same),
- the accuracy value (a registry that now states another figure makes the acknowledgement stale; so does an unstated accuracy
  that is now stated).

A stale acknowledgement is reported as such (`stale_acknowledgement: true` in the 409 context) and the person is asked again.

## Where it is kept, and where it cannot be

| Where the conversion is used | Acknowledgement | Kept |
|---|---|---|
| A **new georeferencing version** recorded by a person (CRS assigned, geotransform entered, control points) | `acknowledge_transformation: true` on the request | In that version's existing JSON `validation.transformation.acknowledgement`: operation, CRSs, accuracy, threshold, who, when. The versions table is append-only, so it is as permanent as the version. |
| The project **area of interest** | `aoi.acknowledge_accuracy: true` | In the AOI's own `aoi_source.transformation.acknowledgement`, with who and when. |
| The stateless **point calculator** (`/crs/transform`) | `acknowledge_operation` and `acknowledge_accuracy_m` query parameters | **Nowhere.** It applies to that one request and is reported as `ACKNOWLEDGED_BY_REQUEST`. |
| A raster **georeferenced by its own file** | not possible | Blocked (below). |
| A recorded version made **before** this existed | not possible | Blocked (below). |

The acknowledgement is built from the refusal itself, not typed in: it names exactly the operation, CRSs and accuracy the
application refused, so a person cannot acknowledge something other than what was shown.

**A request can never unlock stored data.** For a stored raster the acknowledgement comes only from what was recorded with its
georeferencing version. `acknowledge_*` parameters on tile, pixel, layer and similar routes are ignored; the browser's state is
never a source of consent.

### Blocked without a schema change (decision D1)

A raster that uses its own file's georeferencing has no georeferencing version to carry an acknowledgement, and the application
refuses to add one to a raster whose own georeferencing is valid (`ALREADY_GEOREFERENCED`). An old recorded version cannot be
amended (append-only) and recording the same values again is refused (`CHANGE_PRESENT`). Recording an acknowledgement would
need a new kind of record or a new georeferencing operation, which is a schema change.

So these cases **fail closed** with an explanation: the raster is reported (`TRANSFORMATION_BLOCKED` /
`TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED`), is not drawn, is not looked up by position, and its longitude/latitude are absent
with the reason. Its own pixel values and native coordinates are unaffected. Nothing is stored in `audit_event` as a
substitute, and no georeferencing operation is mislabelled to carry one. Lifting this needs an owner decision on a migration.

This is a change in behaviour: a Kalianpur 1962 / UTM 43N GeoTIFF used to be drawn at once with a 999 m caveat. It is now
listed as blocked until its CRS can be given a recorded version that carries an acknowledgement.

**Not yet measured:** how many stored rasters or georeferencing versions are affected. Counting them needs a database read that
has not been authorised. It is pending.

## What the API says

`409` problem responses gain a machine-readable `context`:

```json
{
  "code": "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED",
  "title": "Acknowledge the transformation accuracy",
  "context": {
    "operation_id": "EPSG:1247",
    "operation_name": "Inverse of UTM zone 43N + Kalianpur 1962 to WGS 84 (1)",
    "accuracy_m": 999.0,
    "threshold_m": 10.0,
    "reason": "accuracy_above_threshold",
    "source_crs": "EPSG:24313",
    "target_crs": "EPSG:4326",
    "stale_acknowledgement": false
  }
}
```

`reason` is `accuracy_above_threshold` or `accuracy_not_stated`. Operations in a plan carry `requires_acknowledgement`,
`acknowledgement_reason` and `acknowledgement_threshold_m`. Provenance records (footprints, pixel lookups, measurements)
carry `operation.requires_acknowledgement` and `operation.acknowledgement`.

## What the screens do

- The **georeferencing review** and **control points** review show the refusal, the operation, the figure and the limit, and an
  unticked checkbox with the statement. Nothing is pre-ticked. "Check again with this acknowledgement" validates again; the
  acknowledgement is cleared when the values, the CRS or the chosen operation change.
- **New project** shows the same for an area of interest.
- The **map readout** asks before showing a coordinate through such a shift, says it is for that readout only and that nothing
  is stored.
- The operation table marks operations that need acknowledgement.

## Tests

`tests/test_transform_acknowledgement.py` (policy boundary at 10 m and 10.0001 m, unknown accuracy, stale acknowledgements,
ballpark and grid not unlocked, several operations, structural ballpark detection, request acknowledgements rejected for stored
data, the fail-closed file and legacy cases, the stored version record) and the updated `test_transform*.py`,
`test_crs_assessment.py` and `test_alignment.py`.
