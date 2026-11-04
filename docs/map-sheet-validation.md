# Map sheet validation harness

An offline, repeatable check of georeferenced map sheets against independent reference points. It exists so that, when real
Open Series Map (OSM) and Everest Series Map (ESM) sample sheets are available and authorised, "does this sheet land where it
should" can be answered the same way every time.

**No real OSM or ESM sample has been supplied, so no real map sheet has been validated.** Run on nothing, or on synthetic
files, the harness says **NOT VALIDATED**. Everything this project has tested so far uses synthetic rasters.

## Run it

```powershell
.\scripts\validate-map-sheets.ps1 -Manifest C:\path\to\manifest.json [-OutDir C:\path\to\reports]
```

or `uv run --no-sync python -m geopoc.validation.mapsheets --manifest ... --json out.json --markdown out.md` in `services\api`.

It writes `map-sheet-validation.json` and `map-sheet-validation.md`. Exit status: **0** every real sheet validated; **1** a real
sheet failed or the manifest is invalid; **3** NOT VALIDATED (no real, authorised sample in the manifest).

## What it will and will not do

- It reads the sheets. It writes only the two reports. It does not touch the database, does not read `.env` (the wrapper does not
  load `scripts\env.ps1`), imports no network client, starts and stops nothing, and sends nothing anywhere. PROJ's network is
  off. Tests run it with sockets, the database engine and the settings loader replaced by functions that fail.
- A CRS is an **authority code** (`EPSG:32643`). A name, a series label or a file name never selects one; such a manifest is
  refused. A code the local registry does not have is reported as "cannot be checked", never substituted.
- It guesses no datum, ellipsoid, projection or transformation parameter. It defines no ESM CRS.
- A datum shift worse than 10 m or with no stated accuracy is reported as needing acknowledgement; the harness does not
  acknowledge anything on anyone's behalf. Positions are scored in the sheet's own CRS, so this does not stop a check.
- A sheet is **VALIDATED** only if it is declared `real`, names who authorised using the sample, and every reference point
  that could be scored is within its tolerance of where the sheet's georeferencing puts it. A `synthetic` sheet is only ever
  "SYNTHETIC (self-test only)", and a manifest with no real sheet is NOT VALIDATED however well the synthetic ones do.

## Manifest

```json
{
  "manifest_version": 1,
  "sheets": [
    {
      "id": "sheet-001",
      "provenance": "real",
      "authorisation": "Who authorised using this sample, and for what",
      "series": "Open Series Map",
      "file": "samples/sheet-001.tif",
      "declared_crs": "EPSG:32643",
      "tolerance_m": 25,
      "control_points": [
        {"label": "C1", "column": 120.5, "row": 80.0, "x": 500120.0, "y": 2999200.0}
      ],
      "reference_points": [
        {"label": "R1", "column": 400.0, "row": 300.0, "x": 504000.0, "y": 2997000.0, "uncertainty_m": 5}
      ]
    }
  ]
}
```

- `file` is relative to the manifest. If the file has a geotransform it is used; otherwise (or if `control_points` are given)
  an affine fit to the control points is made with the same code the application uses, and checked the same way (residuals in
  metres, leave-one-out, the exact-fit warning for three points).
- `reference_points` must be points that were **not** used to georeference the sheet, taken from a source independent of it,
  each with the `uncertainty_m` of its coordinate. That uncertainty is never assumed.
- `x`, `y` are in the declared CRS, `x` being its first coordinate whichever way that axis runs.
- `series` is a label for the report and changes nothing.

## What the report contains

Per sheet: file SHA-256, raster size, the datum-shift plan to WGS 84 (status, operation, accuracy, whether it needs
acknowledgement), whether the footprint can be placed (and why not), the control-point fit if one was made, each reference
point's error in metres, RMS and worst error, the tolerance, and a verdict. Overall: the result and the reason.

## Limits

This checks that a sheet's georeferencing agrees with the points the manifest supplies. It does not establish that the points are
right, that a sheet printed in another projection is well represented by one affine map (residuals will show it, and
that is what they are for), or anything about the sheet's content. It validates nothing about a sheet for which no reference
points exist.
