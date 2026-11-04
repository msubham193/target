# Google Earth Engine feasibility prototype

Status (2026-10-29): offline prototype built and tested. **No request has ever been sent to Earth Engine, no account or credential
was used, and no real export was performed.** Earth Engine is disabled by default and is not wired into the application's API,
user interface, start-up or database. This document says what exists, what the research found, and what the account owner must
decide and provide before a real test.

## 1. Summary

| Question | Answer |
|---|---|
| Can Earth Engine supply suitable imagery for this app? | Plausibly yes for Sentinel-2 (10 m, 5-day revisit, global since 2017-03-28, cloud metadata per scene). **Not verified live.** |
| Does the existing app accept what it would export? | Yes, proven offline: an export shaped like Earth Engine's (multi-band `uint16` GeoTIFF in a UTM CRS) is ingested, georeferenced and placed on the map from its own CRS (`tests/test_satellite.py`, scratch database). |
| What blocks a live test? | Account-owner decisions (section 3), a registered Cloud project, credentials kept outside the repository, and the `earthengine-api` package (a new dependency: not installed, not added). |
| Cost? | Noncommercial use is free within a monthly compute quota. Commercial use is paid. Which one applies is the account owner's call (section 3). |

## 2. What was built

Package `services/api/geopoc/satellite/` (nothing imports it at start-up):

| File | Purpose |
|---|---|
| `models.py` | Area of interest (EPSG:4326 polygon), date range, scene query, scene metadata, export plan, and every limit. Refuses an over-large request before anything is sent. |
| `gate.py` | The two switches that keep Earth Engine off, and `redact()`, which strips tokens, private keys and signed URLs from every message. |
| `provider.py` | The interface the application would depend on (`search`, `export_geotiff`). |
| `mock.py` | A deterministic **synthetic** provider: invented scenes and pixels, no network, no credentials. Every record it writes says `synthetic: true`. |
| `earthengine.py` | The real provider, written from the documentation. Reachable only through `EarthEngineProvider.from_settings`, which refuses unless both switches are on, imports `ee` only then, and holds only a project id and a key-file *path*. **Untested against the live service**; tested only with a stand-in `ee` module that checks what our code asks for. |
| `export.py` | Bounded export into a dedicated scratch folder, a second check of the delivered file, and the provenance record. Never overwrites; leaves nothing behind on failure. |
| `__main__.py` | `python -m geopoc.satellite plan | search | export`, mock by default. |

Settings added to `config.py` (all off or empty): `GEOPOC_EARTH_ENGINE_ENABLED=false`, `GEOPOC_EARTH_ENGINE_PROJECT`,
`GEOPOC_EARTH_ENGINE_CREDENTIALS_FILE`. `GEOPOC_ONLINE_ENABLED` keeps its default (false) and is **also** required: Earth Engine
contacts a Google service, so both must be switched on by the account owner. `.env.example` lists them as comments only;
`.gitignore` gained a safety net for key and credential file names.

No migration, no API endpoint, no UI, no change to ingestion, health, models or the online defaults.

### Limits (code constants; settings may lower but never exceed Earth Engine's own caps)

| Limit | Value | Why |
|---|---|---|
| Export region | bounding box of the area, at most 100 km² | keeps an export small and quick |
| Grid | at most 1024 px per side, 1,000,000 px | well under Earth Engine's 10,000 px side cap |
| Bands | at most 4 per export, only listed Sentinel-2 bands | |
| Size | estimated at most 16 MiB (`uint16`) | under the 32 MB `getDownloadURL` cap; the delivered file is checked again |
| Pixel size | 10, 20 or 60 m; never finer than the coarsest band requested | avoids passing resampled data off as detail |
| Dates | at most 366 days, from 2017-03-28, not in the future | |
| Search | at most 100 scenes, area at most 10,000 km² | |
| Output CRS | the WGS 84 / UTM zone of the area's centre, stated explicitly | so the file is georeferenced by a known CRS |

### Provenance record

Beside each export, `<name>.provenance.json` holds: whether the data is synthetic; provider and cloud project; collection id and
title; scene id, acquisition time (UTC), cloud percentage and its definition, MGRS tile, processing baseline; the file's name,
SHA-256, size, dimensions, CRS, pixel size, bounds and band names (all read back from the delivered file); the requested area,
bands, scale, CRS and the exact provider request; the processing statement (single scene, no compositing, no cloud masking,
values are digital numbers, reflectance = value / 10000); attribution and licence summary; tool version; notes. The downloaded
GeoTIFF is **not modified**. After ingestion, the stored original's SHA-256 equals the one in the record, which is how the two
are matched. Nothing is written to the database (that would need a schema decision; see section 8).

## 3. Needs the account owner (not done, not assumed)

1. **Approval to contact Google at all.** The local-first rule means requests (the area and dates, and the Cloud project
   identity) leave the machine. Both `GEOPOC_ONLINE_ENABLED` and `GEOPOC_EARTH_ENGINE_ENABLED` must be set by the owner.
2. **Which kind of use this is, and the matching registration.** Earth Engine is free only for noncommercial use by individuals,
   non-profits and academic institutions; the Terms say noncommercial activities do not include using the service to sell a
   product or service, and that government research needs a defined scope and testable hypothesis and may not meet a long-term
   operational need or repeated periodic tasks. Private companies and government users with operational use must register a paid
   commercial project. **I cannot make this determination and did not.** Using the unpaid tier for commercial or operational work
   would breach the Terms.
3. **A Google Cloud project** registered for Earth Engine (noncommercial: the eligibility questionnaire in the Cloud console, with
   periodic re-verification; commercial: a billing account and plan), with the Earth Engine API enabled and the right IAM roles.
4. **Credentials, kept outside the repository.** Either a person runs `earthengine authenticate` (stored under
   `~/.config/earthengine/credentials`), or the owner creates a service-account key and points
   `GEOPOC_EARTH_ENGINE_CREDENTIALS_FILE` at it. Whether a service account is acceptable for a noncommercial project was not stated
   on the pages read; confirm it.
5. **Approval to add the dependency** `earthengine-api` (and its `google-auth` stack), which is not installed. Note this machine's
   Windows Smart App Control has blocked native wheels before (see `pyproject.toml`); the package is pure Python but its
   dependencies must be checked when added.
6. **A quota tier** (default Community) and acceptance of quota risk (section 6).
7. **Confirmation of the data licence and attribution wording** (section 5).

## 4. Research findings (read 2026-10-29)

Sources, all Google documentation unless stated:
[Access](https://developers.google.com/earth-engine/guides/access),
[Noncommercial quota tiers](https://developers.google.com/earth-engine/guides/noncommercial_tiers),
[Python authentication](https://developers.google.com/earth-engine/guides/auth),
[Terms of Service](https://earthengine.google.com/terms/) (page says last modified 2022-09-29, **older than the quota tiers**),
[Sentinel-2 SR Harmonized catalogue page](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED),
[`ee.Image.getDownloadURL`](https://developers.google.com/earth-engine/apidocs/ee-image-getdownloadurl) (limits taken from search
excerpts of that page, not the full page).

* **Access.** Every call goes through a Cloud project that has the Earth Engine API enabled and is registered as noncommercial or
  commercial. Noncommercial needs the eligibility questionnaire and periodic re-verification; commercial needs billing and a plan.
* **Authentication.** `ee.Authenticate()` (user credentials, stored under `~/.config/earthengine/credentials`) then
  `ee.Initialize(project='...')`. A service account is recommended by Google for unattended code. Default scopes include
  `earthengine`, `cloud-platform` and `drive`.
* **Noncommercial quota tiers** (monthly, per project, reset on the 1st at midnight Pacific; limits are soft): Community 150
  EECU-hours (default, no extra conditions); Contributor 1,000 EECU-hours (needs an active billing account, noncommercial use not
  charged); Partner 100,000 EECU-hours (separate application, review takes up to several weeks, climate-related work). Past the
  quota a project enters "restricted mode": lower concurrency and throughput, still running.
* **Collection.** `COPERNICUS/S2_SR_HARMONIZED`, 2017-03-28 to present, 5-day revisit, bands at 10/20/60 m stored as `uint16`
  with a 0.0001 scale factor; scene properties include `CLOUDY_PIXEL_PERCENTAGE` (granule level) and `MGRS_TILE`; from baseline
  04.00 (after 2022-01-25) values are offset by 1000 to match older scenes; QA60 cloud polygons stopped on 2022-01-25 and were
  rebuilt from other masks from 2024-02-28; 2017-2018 coverage is not yet global. The page names Copernicus Sentinel Data Terms and
  Conditions and gives no attribution string.
* **Export size.** `getDownloadURL`: 32 MB per request and 10,000 px per side. The REST `computePixels` documentation gives
  **conflicting numbers between its versions** (v1alpha: 48 MB, 32K px; v1: 16 MB, 10,000 px, 100 bands). I used the strictest
  figures and did not rely on any of them being current. Larger exports need Earth Engine's batch export to Drive or Cloud Storage,
  which this prototype deliberately does not use (it would need more scopes, storage and a human step).
* **Terms.** The customer owns its data and code; third-party datasets can carry their own licence which governs their use; quotas
  include batch task limits and request rates; the Terms do not discuss bulk downloading and point to an Acceptable Use Policy I did
  not read. **Not read, to be reviewed by the owner:** that policy, the Copernicus Sentinel data terms, and Google Cloud's terms.

Facts I could not confirm from the sources and which therefore need a real test: that `getDownloadURL` with `GEO_TIFF` returns a
plain multi-band GeoTIFF (the prototype refuses a zip rather than handling it), the exact grid snapping Earth Engine applies, and
the exact CRS string of the returned file (the prototype refuses a file whose CRS differs from the requested one).

## 5. Licensing, attribution and provenance

* Sentinel-2 data is free and open under the Copernicus Sentinel terms. The usual credit is "Contains modified Copernicus Sentinel
  data [year]". The prototype writes that into the record for real exports and **never for synthetic data**. This is a summary
  for orientation, not legal advice.
* Google's service terms apply in addition to the data licence. The record says the imagery came through Earth Engine, with the
  Cloud project id (not a credential).
* Provenance is recorded at export time and re-derived from the delivered file (CRS, grid, bands), so it states what was received,
  not only what was asked for. Pixel values are digital numbers; nothing is rescaled, masked, mosaicked or colour-stretched.
  The cloud figure is for the whole granule, not the exported area, and the record says so.

## 6. Cloud processing, cost and quota risks

* **What leaves the machine:** the area of interest (a polygon is sent as a region), the date range, the collection, the Cloud
  project id and the authenticated identity. Earth Engine computes in Google's cloud and returns a file. This is a different
  trust model from the rest of the app and should be said plainly to anyone who reads a project's "local only" setting.
* **Quota:** Community tier is 150 EECU-hours a month per project. A single 100 km², 3-band, single-scene export is small, but the
  EECU cost per request was not measured. Measure it on the first approved test before planning any routine use. Exceeding the
  quota slows work rather than stopping it (restricted mode); commercial projects are billed for usage.
* **Terms and eligibility risk:** a noncommercial registration used for work that is commercial or operational is a breach and
  could end access. This is a decision, not a technical setting.
* **Operational risks:** the signed download URL is itself a credential for a short time (the prototype never logs, stores or
  prints it); tokens and keys must never be placed in `.env.example`, the repository, logs or documentation; accounts may be
  terminated after inactivity; the service can change limits (the tiers changed in April 2026).
* **Dependency risk:** `earthengine-api` pulls in the Google auth stack; its footprint and Windows loading behaviour have not been
  assessed.

## 7. Satellite imagery is not basemap tiles

| | Satellite imagery (this prototype) | Basemap tiles (not part of this work) |
|---|---|---|
| What it is | measured data for a place and time: calibrated bands, a source, an acquisition date, a CRS | a picture drawn for display (roads, labels, or a mosaic), often undated and composited |
| Use | analysis, measurement, comparison over time, detection | context while looking at a map |
| Provenance | recorded per file | usually none, and the terms usually forbid downloading or analysing tiles |
| In this app | an ordinary GeoTIFF upload, placed from its own CRS | none: the map has a plain background and a graticule; the offline basemap decision (U-013) is still open |

Putting a basemap under the map would be a separate decision; nothing here does that.

## 8. Fit with the existing app and open design choices

* A delivered file passes the existing raster ingestion unchanged (valid CRS and geotransform, `uint16` bands, band names kept,
  footprint computed, tiles drawn from the file's own CRS). The stored original is byte-identical to the export.
* **Where provenance lives.** Today the record is a JSON file beside the export, matched by SHA-256. Keeping it with the project
  (so the UI could show source, date and attribution) needs a place in the database, which is a schema change and needs approval.
  Options: a sidecar-style table for asset provenance, or a JSON column on `asset`. Not started.
* **Where it would plug in later,** if approved: a "find imagery" step that calls `search` and shows dates and cloud cover for the
  project's area, then an explicit "export this scene" action that runs `run_export` into a scratch folder and hands the file to
  the normal upload path. Nothing like that exists; no automatic downloads are possible by design.
* Not covered: multi-scene mosaics, cloud masking, time series, other collections (Landsat, Sentinel-1), exports larger than the
  limits, batch exports.

## 9. How a real test would be run (only after approval)

1. The account owner completes section 3 items 1-7 and installs `earthengine-api` in the application's environment.
2. In a **separate scratch shell**, not the running services: set `GEOPOC_ONLINE_ENABLED=true`, `GEOPOC_EARTH_ENGINE_ENABLED=true`,
   `GEOPOC_EARTH_ENGINE_PROJECT=<project>` (and optionally `GEOPOC_EARTH_ENGINE_CREDENTIALS_FILE=<path outside the repo>`).
3. `python -m geopoc.satellite search --provider earthengine --bbox ... --start ... --end ...` (a small area, a one-month range):
   check the dates and cloud figures against the Earth Engine Code Editor.
4. `python -m geopoc.satellite export --provider earthengine --bbox ... --scene <id from the search> --acquired <time from the
   search> --scratch D:\geopoc-data\_satellite_trial` for one small area. Check the file in a GIS tool, then upload it into a
   **scratch project** (a scratch database such as `geopoc_smoke`), never the working project.
5. Record the quota used (Cloud console) and any difference between what the tests assumed and what came back.

## 10. What was and was not tested

* Run: 62 offline tests (`tests/test_satellite.py`) covering the off-by-default switches, every request limit, the mock provider,
  the export and its provenance, refusal of wrong or oversized delivered files, no overwriting, scratch-folder rules, redaction of
  tokens, keys and signed URLs from messages and logs, the Earth Engine provider's calls against a stand-in `ee`, the command line,
  a complete flow with the network blocked, and ingestion of an export into a scratch project on the scratch database. Five
  deliberate faults were each caught by a test.
* **Not run:** anything against the real Earth Engine service. The `earthengine.py` code is unverified against the live API; its
  calls were written from the documentation. No credential was created, read or used.
