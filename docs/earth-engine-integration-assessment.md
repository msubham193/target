# Google Earth Engine integration: assessment (GEE-0)

**Status: assessment only.** Written 2026-10-31. Nothing in this document has been implemented. No source file, schema, data,
dependency, service, credential or cloud resource was changed or created to produce it; the only file added to the repository is
this one. GEE-1 and later phases need separate approval.

**Fixed decisions (from the approval):** organisation authentication, commercial use, the existing FastAPI backend, React
frontend, PostgreSQL/PostGIS database, and the existing map workspace and raster infrastructure.

**A note on the specification.** The approval refers to the "Google Earth Engine Integration Master Implementation
Specification". That text was not available to me in this session; I worked from the 13 assessment points and the architecture,
preservation and deliverable rules in the GEE-0 approval, and from the code. If the specification contains requirements beyond
those, they are not reflected here and the document should be reconciled against it before GEE-1.

**Revision 1 (2026-10-31, after the GEE-0 review).** Documentation-only corrections requested in the review; Appendix B lists
every change. In summary: GEE-7 (final integration and client acceptance) is restored as a separate phase (section 21);
organisation-level Earth Engine authentication is stated not to authorise application users, and the additional
authentication and authorisation required before production use are documented (section 6.6); budget controls are
strengthened to atomic reservations, concurrent-worker protection, recorded actual usage, hard server-side ceilings and a
global kill switch (section 16.3a, 16.4); project-scoped authorisation is mandatory on every remote tile request (section
13.3); commercial-terms approval is an explicit prerequisite for distributing remote previews and retaining exported imagery
(A-02, section 21 gates, section 26); and the standing restrictions are listed in one place (sections 5.4 and 26). The GEE-0
approval's wording was followed; the original master specification was still not available to me, so the content of GEE-7 below
follows the review's one-line description and must be reconciled against that specification.

**Revision 2 (2026-10-31, roadmap clarification).** Documentation-only. Migration L (the usage ledger) remains a proposed,
separately approved migration, but it is now required before **multi-user, multi-worker or production budget-controlled
operation**, not automatically before GEE-2. GEE-1 stays a manual, owner-run evaluation with no migration. GEE-2 and GEE-3 must
remain within a defined **controlled evaluation model** (operating model A, section 16.3a) whenever Migration L has not been
approved and implemented. No commercial, security, AOI-transmission, caching, CRS or data-preservation restriction was
weakened. Appendix B lists every change.

## How to read the evidence tags

| Tag | Meaning |
|---|---|
| **[V-doc]** | Verified: read on Google's or a data provider's documentation page on 2026-10-31. The page is named in Appendix A. |
| **[V-code]** | Verified: read in this repository on 2026-10-31. |
| **[A]** | Assumption. Plausible or common practice, but not shown by anything I read. An organisation administrator or a spike test must confirm it before it is relied on. |
| **[X]** | Not found / not verifiable from public documentation here (for example, current commercial prices). Needs Google, the account owner, or a live test. |

Nothing in this document was tested against the live Earth Engine service. No account, project or credential was used.

## 1. Summary and recommendation

**Verdict.** Integration is feasible inside the existing architecture, without a microservice. The application already has the
three things it needs: a job queue with cancellation, retry and progress; a raster ingestion path that accepts a GeoTIFF with a
CRS; and a map that draws tiles from an API endpoint. The work is mostly new modules in `services/api/geopoc/` plus one new
project screen and one new kind of map layer.

**Eight findings that shape the design.**

1. **There is already an offline Earth Engine prototype** (`geopoc/satellite/`, [V-code]). It is Sentinel-2-only, uses
   `getDownloadURL` for small exports, is off by default, and has never contacted Google. Its request limits, redaction, mock
   provider and provenance record are worth keeping; its single hard-coded collection and its scratch-folder export are not
   enough for this scope. Its documentation assumed noncommercial registration; that no longer applies.
2. **Two different things must stay separate**: a *remote preview layer* (pixels computed and drawn by Earth Engine, never
   stored here) and a *local raster asset* (a GeoTIFF exported from Earth Engine, ingested, checksummed and stored like any
   upload). The design gives them different types, different API shapes, different map badges and different capabilities
   (section 5.3).
3. **Credentials never reach the browser.** The browser only ever sees URLs of this application's own API. A tile proxy in
   FastAPI holds the Earth Engine tile address and token and fetches tiles on the browser's behalf (section 13).
4. **No key file by default.** Google's own documentation recommends Application Default Credentials for unattended code and says
   key files are "not recommended for production environments where ADC can be used" [V-doc]. Organisations created since
   2024-05-03 block service-account key creation by default [V-doc]. The right mechanism depends on **where the backend runs**,
   which is the first client decision (section 6). The managed-identity options are real only on Google Cloud, or through
   workload identity federation from an organisational identity provider; whether Earth Engine's client accepts a federated
   credential is **not** stated in the documentation I read and needs a spike [X].
5. **Organisation-level Earth Engine authentication is not application-user authentication or authorisation.** The
   organisation's Google identity proves to Google that *the application backend* may use the Cloud project; it says nothing
   about *which person* is calling the application, or what that person may do. Local profiles are attribution only, not
   authentication [V-code]: any holder of a profile name can call the API. Anything that spends money (map tiles, exports),
   sends an area to Google, or retains imagery therefore needs, before production use, real user authentication, project
   membership and per-action roles (the unapproved G6 work or an equivalent), in addition to the server-side budget controls
   (section 6.6, 16.3a). Until those exist, the integration may be used only in a controlled, developer-run evaluation, with
   the per-project opt-in (the existing `project.source_preference = allow_online`, default `local_only` [V-code]) and hard
   server limits as the only controls.
6. **The shared CRS converter stays the single authority.** Earth Engine takes the area of interest in longitude/latitude (WGS 84)
   and exports in a CRS the application names. A project AOI entered in another CRS goes through the Phase 1/3 planner first;
   an ambiguous, ballpark-only or grid-missing conversion is refused, never guessed (section 15).
7. **Database changes are needed, but each only when its operating condition is reached.** Persisting exports as assets needs a
   migration, because `asset.origin` is constrained to `upload` or `derived` and `job.kind` to a fixed list [V-code]. Budget
   control that is correct under concurrency (several users, API processes or job workers) needs a **persistent, atomically
   updated usage ledger**, which an in-memory counter cannot give. That ledger migration (Migration L) is a **proposal that
   needs its own separate approval**, and it is required **before multi-user, multi-worker or production budget-controlled
   operation**, not automatically before GEE-2. Until it is approved and applied, GEE-2 and GEE-3 (and any later phase) remain
   inside a strictly limited **controlled evaluation model** (section 16.3a, "Operating models"). GEE-1 is a manual, owner-run
   evaluation with no migration at all. Nothing is applied here, and no migration is made without separate approval.
8. **Commercial-terms approval is a hard gate.** The Terms page read is silent on caching and redistribution, and the commercial
   terms were not read. Written confirmation of the Google Cloud commercial terms (A-02) is an explicit prerequisite for showing
   remote previews to anyone other than the developers in a controlled evaluation, and for retaining exported imagery
   (sections 14.4, 21, 26).

**What blocks GEE-1** is not technical: a commercial Cloud project and billing, an agreed authentication route, approval of the
new dependency, and a decision on data governance (the project area leaves the machine). Section 24 lists them.

## 2. What exists today (verified in the repository)

| Area | What is there | Relevance |
|---|---|---|
| `geopoc/satellite/models.py` | Area of interest, date range, scene query, export plan, limits (100 km², 1024 px side, 4 bands, 16 MiB, 366 days). `COLLECTION_ID` fixed to `COPERNICUS/S2_SR_HARMONIZED`; first date hard-coded `2017-03-28`. | Reuse the validation pattern. Generalise to a dataset registry; read date extents from the catalogue at run time instead of hard-coding them. |
| `satellite/gate.py` | Both `GEOPOC_ONLINE_ENABLED` and `GEOPOC_EARTH_ENGINE_ENABLED` required; `redact()` strips tokens, private keys, signed URLs. | Keep as is. Extend patterns to tile URLs and to Cloud Storage signed URLs. |
| `satellite/earthengine.py` | Real provider written from documentation, untested live. `ee.ServiceAccountCredentials(email, keyfile)` path; `getDownloadURL` export; search by `CLOUDY_PIXEL_PERCENTAGE`. | Replace the key-file path (section 6). Imports `httpx` at module level. |
| `pyproject.toml` | `httpx` is in the **dev** dependency group only; `earthengine-api` absent. `requires-python >=3.11,<3.12`. | A runtime dependency gap: a production install that omits dev groups could not run the existing download code. `httpx` must move to runtime in GEE-1. |
| `satellite/export.py`, `mock.py` | Bounded export into a `_satellite*` scratch folder, second check of the delivered file, provenance JSON beside it; a synthetic provider. | Provenance fields are a good base for the database record. The mock is the offline test double. |
| `config.py` | `earth_engine_enabled`, `earth_engine_project`, `earth_engine_credentials_file` (a *path*), all off. | Replace the credentials-file setting with an auth-mode setting (section 6). |
| Jobs | `job` table is the queue; claim with `SKIP LOCKED`; stages, progress, cancel, retry with back-off, transient vs permanent errors; handlers run in a child process. `ck_job_kind` limits kinds. | Fits asynchronous exports. A new kind needs a migration. |
| Ingestion | `ingest(session, project, profile, filename, modality, temp_path, sha256, …)` stores the original once, extracts CRS/geotransform, computes the footprint. `asset.origin` is `upload` or `derived` (`ck_asset_origin`). | A downloaded GeoTIFF can go through it unchanged. A new origin value `online_import` (named in the original blueprint) needs a migration. |
| `asset_lineage` | Parent and child must both be real assets; relation limited to `pixel_difference_of`. | A remote source cannot be a lineage parent. Provenance needs its own table. |
| Layers and tiles | `GET /projects/{id}/layers` returns `rasters`, `excluded`, `aoi`, `basemap`; tiles are `GET /assets/{id}/tiles/{z}/{x}/{y}.png`, Web Mercator, `Cache-Control: private`. Tile endpoints do not require a profile header. | Remote layers get a separate array and endpoint. A tile that costs money needs stronger gating than the local tile route has. |
| Map | MapLibre; the style references no online source; plain background and graticule; decision U-013 (basemap) open. | Remote tiles come from this API, so the "no online source in the style" rule holds. |
| CRS | `geo/convert.py` and `geo/transform.py` (Phase 1/3). Guard test forbids direct PROJ `Transformer` construction outside a reviewed list. | Every Earth Engine-related conversion goes through them (section 15). |
| Project | `source_preference ∈ {local_only, allow_online}`, default `local_only` (`ck_project_source_preference`); `aoi_geom` (EPSG:4326) and `aoi_source` (as entered, with transformation record). | Natural per-project gate for any outbound call, with no schema change. |
| Frontend | Routes include `/projects/:id/satellite`, which is the **existing overhead-raster analysis page**, not Earth Engine. | The new screen needs a different name, for example `/imagery` (section 18). |

## 3. Earth Engine capabilities verified from documentation

All items are from pages read on 2026-10-31 (Appendix A). Where two pages disagree, section 3.2 says so.

### 3.1 Verified

| # | Fact | Tag |
|---|---|---|
| V1 | Every Earth Engine call, from any client, goes through a Cloud project that has the Earth Engine API enabled, is registered for commercial or noncommercial use, and grants the caller the right roles. | [V-doc] |
| V2 | Commercial access is configured by choosing a plan on a billing account; noncommercial needs only a questionnaire and no billing. The only commercial plan name the access page gives is "Limited". No prices are on that page. | [V-doc] |
| V3 | To stop compute charges, disable the Earth Engine API. Stored assets keep incurring storage charges even with the API disabled. | [V-doc] |
| V4 | The Terms of Service say the free service is for non-commercial activities and government research (§2.1(c)); commercial use is by fee under Google Cloud commercial terms (page banner). Selling a product or service, or charging for outputs, is not non-commercial (§2.1(a)). | [V-doc] |
| V5 | Third-party datasets may carry their own licence, which governs if it conflicts (§4.4). The Terms say Google may terminate after 180 days of inactivity (§7.3) and may suspend for acceptable-use violation if not fixed within 24 hours (§3.1). The page is the older consumer-style Terms; the commercial terms are Google Cloud's and were not read. | [V-doc] |
| V6 | Predefined roles: `roles/earthengine.viewer` (view and list assets and tasks), `roles/earthengine.writer` (also run interactive computations and create long-running export tasks), `roles/earthengine.admin`, `roles/earthengine.appsPublisher`. | [V-doc] |
| V7 | `roles/serviceusage.serviceUsageConsumer` is needed to use a project as the active project; without it `ee.Initialize(project=…)` fails. The permission `earthengine.computations.create` covers running expressions, batch exports and interactive results (maps, thumbnails, charts). | [V-doc] |
| V8 | Service accounts get access automatically once the parent project is registered and the API enabled, but still need permissions inside the project. | [V-doc] |
| V9 | `ee.Initialize(project=…)` requires a project in Python. `ee.Initialize(credentials=…)` accepts credentials from elsewhere. Without them it uses stored user credentials or `google.auth.default()`. | [V-doc] |
| V10 | The service-account guide: Application Default Credentials is "recommended for unattended environments"; a private-key file is "not recommended for production environments where ADC can be used"; the guide says to keep key files safe. | [V-doc] |
| V11 | ADC looks, in order, at `GOOGLE_APPLICATION_CREDENTIALS`, then the file made by `gcloud auth application-default login` (which may hold an impersonated service account), then the attached service account on Google Cloud. A file named by the variable may be a service-account key, a Workload Identity Federation configuration or a Workforce Identity Federation configuration. The attached service account is "the preferred method" in production; keys "create a security risk". | [V-doc] |
| V12 | Organisations created on or after 2024-05-03 have a baseline policy preventing persistent service-account keys (some created from February 2024 too). The constraint name differs between pages (`iam.disableServiceAccountKeyCreation` vs `iam.managed.disableServiceAccountKeyCreation`). | [V-doc] |
| V13 | Default quotas: 40 concurrent requests, 100 requests/s per project and per account (adjustable); 429 on overload, with the client library retrying up to five times with exponential backoff; 2 average concurrent batch tasks (for commercial use, set by the plan); 3,000 tasks in READY state; request payload 10 MB; aggregation results 100 MiB. An optional per-day EECU-time quota (default unlimited) is "approximate" and "not designed to strictly limit EECU-time". Circumventing quotas with several accounts breaks the Terms. | [V-doc] |
| V14 | Quotas are viewable and changeable in the Cloud console; a metric `earthengine.googleapis.com/daily_eecu_usage_time` exists. | [V-doc] |
| V15 | Exports go to Drive, Cloud Storage or an Earth Engine asset. Cloud Storage needs a billed project, a bucket and write permission on it. Python uses `ee.batch.Export.image.toCloudStorage(...)` and `task.start()`. Default file format GeoTIFF; `formatOptions` supports `cloudOptimized: true` and `noData`. Large GeoTIFF exports are split into tiles named `baseFilename-yMin-xMin`. Resampling is nearest neighbour by default if the grid differs. `crs` plus `crsTransform` gives full control of the pixel grid; `scale` alone can shift the output relative to the source. Google Drive export is not supported under VPC Service Controls. | [V-doc] |
| V16 | Visualisation parameters: `bands` (three names → RGB), `min`, `max`, `gamma`, `palette` (single band only). Default stretch depends on type (floats [0,1]; 16-bit uses full range). | [V-doc] |
| V17 | `earthengine-api` on PyPI: version 1.7.50 at the time read; `requires_python >=3.11`; depends on `google-cloud-storage`, `google-api-python-client>=2.201.0`, `google-auth>=2.59.1`, `google-auth-httplib2>=0.4.4`, `httplib2>=0.32.0,<1`, `requests`. Whether the wheel is pure Python was **not** confirmed. | [V-doc] |
| V18 | Datasets: see section 7 for identifiers, bands, scale factors, date extents and licences. | [V-doc] |

### 3.2 Documentation conflicts and gaps

| Item | What the pages say | Treatment |
|---|---|---|
| Dataset end dates | The Sentinel-1, Landsat 9 and Cloud Score+ pages each show a different end date in their summary text and their metadata block. | The backend must read each collection's extent from the catalogue at run time and must not hard-code ends. |
| Download-URL and `computePixels` limits | Earlier research (2026-10-29, `docs/earth-engine-feasibility.md`) found 32 MB / 10,000 px for `getDownloadURL` and conflicting `computePixels` figures between API versions. This session's fetch of the `computePixels` page returned only navigation text. | Keep the stricter figures as ceilings; re-check in the GEE-1 spike. |
| Commercial prices and included credits | The Google pricing page returned no readable figures; secondary sources quote different platform fees, included credits and per-EECU-hour rates, and some are years old. | **No price is used in this document.** A written Google quotation is required [X]. |
| Map ID lifetime | I found no statement of how long a map ID or token returned by `getMapId` stays valid. | Design for expiry (re-create the map from the stored recipe on a 401/403/404) [X]. |
| Federated / impersonated credentials with the Earth Engine client | The Earth Engine pages say ADC is recommended and that `ee.Initialize(credentials=…)` accepts credentials. They do not mention workload identity federation or impersonation. | Plausible because the client uses the Google auth library, but unconfirmed. A GEE-1 spike must prove it [X]. |
| Tile caching and serving tiles to others | The Terms page does not address caching or redistributing outputs/tiles beyond §2.1(a) and §2.2(c). | Treat persistent tile caching as not permitted until the Cloud commercial terms are read and confirmed [A]. |
| Data location | No page I read states where Earth Engine processes data. | Governance question for the organisation [X]. |

## 4. Assumptions requiring organisation-admin confirmation

Each must be answered before the phase in the last column starts.

| ID | Assumption | Why it matters | Needed by |
|---|---|---|---|
| A-01 | A **dedicated Cloud project** for this application will be registered for **commercial** use, with a billing account and an agreed plan, and the Earth Engine API enabled. | All calls and costs attach to it. The noncommercial registration must not be used. | GEE-1 |
| A-02 | **Hard gate.** The Google Cloud commercial terms (not the older consumer Terms page) have been read and approved **in writing** by the organisation for: distributing remote previews (serving Earth Engine tile previews to anyone beyond the developers in a controlled evaluation), delivering exported imagery to the client, **retaining exported imagery** (including the stored original GeoTIFFs), and any caching of tiles. | The Terms page I read is silent on caching and redistribution, and the commercial terms were not read. Until A-02 is satisfied: previews are shown only to the developers in a designated test project; exports made during evaluation are not retained or delivered (they are deleted after the test); no tile is cached. | GEE-1 (evaluation limits); **before GEE-3 exits and before any GEE-4 export is retained** |
| A-03 | **Where the backend runs**: a Google Cloud VM or Cloud Run (attached identity available), or an on-premises or local Windows host (it is not). | Decides the credential mechanism (section 6). | GEE-1 |
| A-04 | If not on Google Cloud: the organisation has an **OIDC/SAML identity provider usable for workload identity federation**, or an administrator will grant an exception to the key-creation policy. | The only alternatives to a long-lived key. | GEE-1 |
| A-05 | The service identity can be given exactly: `earthengine.computations.create` (through `roles/earthengine.writer` or a custom role), `roles/serviceusage.serviceUsageConsumer`, and object read/write on one bucket, and nothing else. A viewer-only role is not sufficient for compute. | Least privilege. A custom role with only the computation permission may work; unverified. | GEE-1 |
| A-06 | **Public catalogue datasets in section 7 are readable by a commercial project** without extra entitlement. | The pages describe the datasets; they do not describe per-project entitlement. Authentication does not imply dataset access, so every dataset is probed (section 7.3). | GEE-1 |
| A-07 | **Data governance:** a project's area of interest, date range and chosen bands may be sent to Google. Some projects (for example, sensitive sites) must never be. | Today the app is local-first and `local_only` by default. | GEE-1 |
| A-08 | **Network:** outbound HTTPS to `earthengine.googleapis.com`, `oauth2.googleapis.com`, `www.googleapis.com`, `storage.googleapis.com` is allowed from the host; any TLS inspection is compatible. The tile host returned by `getMapId` is unverified and must be checked in the spike. | Corporate proxies commonly break this. | GEE-1 |
| A-09 | **Budget:** a monthly spending ceiling, alert thresholds, and who receives alerts; an agreed daily EECU-time quota and concurrency ceiling. | Section 16. | GEE-1 |
| A-10 | **Retention:** how long remote recipes, exports and the Cloud Storage bucket contents are kept; who may delete. | Section 14, 16. | GEE-4 |
| A-11 | **Attribution wording** for Sentinel (Copernicus), Landsat (USGS) and Earth Engine on maps, reports and exports, and who signs it off. | Section 16.5. | GEE-3 |
| A-12 | **Who may create layers and exports, and how those people are authenticated.** Without real user authentication and the roles work (G6, unapproved) the application cannot tell users apart or limit what they may do; "any profile plus server limits" is acceptable only for a controlled evaluation. | Section 6.6, 16.3a. | Before GEE-3 serves anyone but the developers; mandatory before production use |
| A-13 | The new dependency set (`earthengine-api` and its Google client stack) is acceptable, including a check that every native wheel loads on the machine where Windows Smart App Control is enforcing. | Earlier packages (`rasterio`, `psycopg`, `pyproj`) had to be pinned for that reason. | GEE-1 |
| A-14 | Earth Engine tile images are Web Mercator display images with the same tile grid MapLibre expects. | Common practice; not on a page I read. | GEE-1 spike |
| A-15 | `getMapId` / `computePixels` / a bucket export for the dataset in question can be run by a service identity under a **commercial** project with no extra product enabled (for example, Cloud Storage API). | Bucket exports need a billed project and bucket [V-doc]; other APIs may be required. | GEE-4 |
| A-16 | The organisation has, or will provide, an identity provider and a user/role model that the application can rely on (single sign-on or an application login, project membership, role assignment, and a way to remove a person's access). | Organisation-level Earth Engine authentication does not authorise application users (section 6.6). | Before production use |
| A-17 | The persistent usage ledger (Migration L, section 16.3a, 19) may be added by a separately approved migration, and the organisation will set the spending ceilings it enforces. | Correct budget control under concurrency needs persistent, atomic state. Not needed while operation stays inside the controlled evaluation model. | Before multi-user, multi-worker or production budget-controlled operation (not automatically before GEE-2) |

## 5. Architecture

### 5.1 Principles

1. **One backend.** The existing FastAPI process and the existing worker process do all Earth Engine work. No microservice: the
   load is modest, the job queue exists, and a new service would need its own deployment, credentials and authentication.
   A separate service would be justified only if Earth Engine calls had to run on a different network or identity than the
   rest of the application; nothing found so far requires that.
2. **Credentials stay in the backend.** The browser never receives a token, key, map ID, signed URL or Cloud project identifier.
3. **Remote stays distinguishable from local** in types, API, storage, map and reports.
4. **Nothing is automatic.** No scene is exported, no tile requested, and no area sent because a page loaded, a project was
   created or opened, a schedule fired or a background job ran. Every transmission of an area of interest (or any value derived
   from it) to Google is the direct result of one explicit person action, after a notice that states what will be sent, and is
   recorded in the audit history. The project opt-in permits the action; it is not consent to every later transmission.
5. **The shared converter and planner decide every coordinate change.** No silent fallback.
6. **Offline remains the default.** With the switches off (or the project `local_only`), the application behaves exactly as today.

### 5.2 Components

```
Browser (React + MapLibre)
   │  only this app's own URLs; never a Google URL, token or project id
   ▼
FastAPI (existing)                                   Worker (existing job runner)
 ├─ routers/earth_engine.py   (status, datasets,      ├─ handler: ee_export
 │                             scene search, layers,  │    start EE task / getDownloadURL
 │                             exports, usage)        │    poll, cancel, download, verify,
 ├─ routers/remote_tiles.py   (tile proxy)            │    ingest() ➜ local asset
 ├─ earth_engine/                                      │
 │    client.py     (init, credentials, retries)      │
 │    catalogue.py  (dataset registry + probe)        │
 │    recipes.py    (validate; build ee expression)   │
 │    masks.py  indices.py  visual.py                 │
 │    budget.py     (ledger, limits, kill switch)     │
 │    aoi.py        (project AOI ➜ EPSG:4326 through geo.convert)
 │    provenance.py (record shape)                    │
 └─ satellite/ (existing prototype: gate, redact, models, mock)
        │                                             │
        ▼                                             ▼
   Earth Engine REST (interactive)            Earth Engine batch ➜ Cloud Storage bucket
                                                     │ (private, short lifecycle)
                                                     ▼
                                              download ➜ verify ➜ ingest ➜ local asset
```

The existing `geopoc/satellite/` package is kept and grown into the provider layer (its `SatelliteProvider` protocol already
isolates the application from the service). New code is in a sibling package so the prototype's tests keep passing during the
migration; the two are merged once the new one covers the old one's tests.

### 5.3 Remote layer vs local asset

| | Remote preview layer | Local raster asset (from an export) |
|---|---|---|
| Where the pixels are | Earth Engine; computed per tile on demand | A GeoTIFF in the local store, checksummed |
| API type | `RemoteLayerOut` (`kind: "remote"`, `source: "earthengine"`) in `LayersOut.remote_layers` | `RasterLayerOut` in `LayersOut.rasters`, `asset.origin = online_import` |
| Reproducible | By its **recipe** (dataset, scene ids or window, mask, visualisation) and the recorded dataset version; the pixels may change if Google reprocesses | By the stored original and its SHA-256 |
| Works offline | No | Yes |
| Pixel values, statistics, measurement of pixel values, comparison, 3D, georeferencing tools | **Not available.** Only position-based tools (WGS 84 distance/area and annotation) | All existing tools |
| Map badge | "Remote – Earth Engine", cloud icon, dashed outline, no georeferencing panel | Normal raster, plus a "From Earth Engine" source badge |
| Included in reports and exports | Only if explicitly saved as a local asset; otherwise labelled and attributed | Yes, with attribution |
| MapLibre source id | `ee-<layer id>-<recipe hash>` | `raster-<asset id>-v<n>` (existing) |

A person turns a remote layer into a local asset with an explicit **Save as local raster** action, which creates an export job
(section 14). The reverse never happens.

### 5.4 Standing restrictions (unchanged by this revision)

These apply to every phase; the full list is in section 26.

- No database migration without separate approval, each time.
- No credentials in browser code or in any browser-visible response.
- No automatic outbound transmission of an area of interest (principle 4).
- No persistent tile caching until the commercial terms are confirmed in writing.
- No coordinate transformation outside the shared converter (`geo.convert` and the planner).
- No ESM, Everest-56 or Polyconic assumptions, definitions or compatibility claims.

## 6. Authentication and credential management

### 6.1 Interpretation

"Organisation authentication" is read as: **Earth Engine is accessed with an identity owned by the organisation, in a Cloud
project owned by the organisation, not with any individual's personal Google account**, and the application never stores a
person's Google password or OAuth refresh token. The application's own profiles remain attribution only. If the intent was
instead "each staff member signs in to Google with their organisation account and Earth Engine sees that person", this is a
different design (OAuth client, consent, per-user quotas, tokens stored per profile) and the section below must be redone. **This
needs confirming (D-02).**

### 6.2 Options, in order of preference

| Rank | Mechanism | Works when | Long-lived secret? | Evidence | Verdict |
|---|---|---|---|---|---|
| 1 | **Attached service account** (Cloud Run / Compute Engine / GKE) read through ADC | The backend runs on Google Cloud | None | [V-doc] ADC order and "preferred method"; Earth Engine guide says ADC for unattended code | **Preferred**, if A-03 says the host can be on Google Cloud. |
| 2 | **Workload Identity Federation** (an organisational OIDC/SAML provider exchanges its token for a short-lived Google token; the credential configuration file contains no key), read through ADC | Off-Google-Cloud host with an IdP | The IdP's token source (a file or endpoint) must itself be protected; the config file is not a key | ADC supports WIF configuration files [V-doc]. That Earth Engine accepts them is **[X]** | Best off-cloud option. **Requires a spike.** |
| 3 | **Service-account impersonation** through ADC (`gcloud auth application-default login --impersonate-service-account`) | A person logs in on the host | Refresh token of that person | [V-doc] ADC may hold an impersonated service account | Acceptable for development and demonstration only: it ties the service to one person's session. |
| 4 | **Service-account key file** (JSON) | Anywhere | **Yes, long-lived** | [V-doc] "not recommended"; baseline policy blocks it by default | **Last resort**, only with a written administrator exception. |
| — | Individual user credentials (`ee.Authenticate()` as a person) | Development | Refresh token | [V-doc] | Not for the application. |

### 6.3 If a key file is unavoidable (conditions)

- Created by an administrator under a time-limited exception; key restricted to one service account with only the A-05 roles.
- Stored **outside** the repository and outside the data folder, readable only by the service user. On Windows, an ACL-protected
  file or the Windows credential store (DPAPI); never in `.env`, never in the database, never in a log.
- Rotation at most every 90 days [A: common practice]; an alert for use outside expected hours or from an unexpected address.
- The application stores **only a path or a reference**, as the prototype's settings already do ([V-code]); it never reads the
  key into settings.
- A startup check refuses to run if the file is world-readable or inside the repository.

### 6.4 Backend handling (all options)

- **One initialisation point** (`earth_engine/client.py`): reads the auth mode and project id from settings, builds credentials
  with the Earth Engine scope, calls `ee.Initialize(credentials=…, project=…)`, and exposes a health state (`ok`,
  `not_configured`, `auth_failed`, `quota`, `disabled`). No other module imports `ee`.
- **Never logged:** tokens, keys, signed URLs, map IDs. The existing `redact()` patterns are extended (Cloud Storage signed URLs,
  `X-Goog-Signature`, map IDs). Exceptions from the Google libraries are wrapped, as the prototype already does.
- **Settings** replace the credentials-file setting with: `GEOPOC_EARTH_ENGINE_AUTH_MODE` (`adc`, `wif`, `impersonation`,
  `key_file`), `GEOPOC_EARTH_ENGINE_PROJECT`, and, only for `key_file`, the path. Auth mode `adc` covers modes 1–3 because
  ADC resolves them. None of these values is a secret except the file's contents.
- **Status endpoint** reports mode, project id (it is not a secret but is shown only to maintainers), connection state and last
  check time, never a credential.
- **Connection test** is an explicit action, rate-limited, that does `ee.Initialize` and one tiny computation.
- **Least privilege** per A-05; separate identities for interactive tiles and for batch export are possible but not required.
- **Secrets in the browser:** none. MapLibre's `transformRequest` is not used to add any header for these layers.
- **Revocation:** the kill switch (section 16.4) stops all calls without a restart (read at each request); disabling the Earth
  Engine API in Cloud stops compute charges [V-doc].

### 6.5 Dataset access is not implied by authentication

A successful `ee.Initialize` proves that the identity may use the project. It does not prove that the identity may read every
dataset (private assets, licensed datasets, or datasets restricted by organisation policy). Section 7.3 defines a per-dataset
probe whose result is stored and shown, and every layer or export is refused with `EE_DATASET_UNAVAILABLE` when its dataset has
not passed the probe.

### 6.6 Organisation authentication is not application-user authorisation

Two separate questions, answered by two separate mechanisms:

| Question | Answered by | Status |
|---|---|---|
| May **the application backend** use the Cloud project and Earth Engine? | The organisation's Google identity (sections 6.1–6.5) | Designed here |
| Which **person** is calling the application, and what may that person do? | Application user authentication and authorisation | **Not provided by Earth Engine authentication, and not provided today.** Local profiles are attribution only [V-code]; the `X-Workspace-Profile` header is a claim, not a proof. |

Because every request reaches Earth Engine as the same organisational identity, Google's quotas, billing and audit logs cannot
distinguish one application user from another. Any control that depends on *who* is asking must therefore be enforced by the
application itself.

**Additional requirements before production use** (all must be met; none is built or approved by GEE-0):

1. **User authentication.** Each person signs in through the organisation's identity provider (or an application login the
   organisation approves); the API accepts only verified sessions or tokens, not a free-text profile. Sessions expire and can be
   revoked. The API is not reachable by unauthenticated callers on any interface other than loopback.
2. **Project membership.** A person may act on a project only if they are a member of it. Every Earth Engine endpoint, tile
   request and export checks membership on the server; the UI hiding a button is not a control.
3. **Per-action roles.** At least: *viewer* (see existing remote layers), *analyst* (search, create layers), *exporter* (start
   exports), *Earth Engine administrator* (enable per project, view usage, change ceilings within the organisation's limits,
   operate the kill switch). The right to *send an area to Google* is a separate permission from the right to view results.
4. **Attribution that can be trusted.** Audit events, usage ledger rows and export records carry the authenticated user's
   identifier, not a self-declared name.
5. **Request integrity.** Cross-site request protection on state-changing calls; no credentials or tile tokens in URLs that are
   logged or shared (section 13.3 limits what a tile token can do).
6. **Per-user and per-project limits** in addition to global ceilings (section 16.3a), so one person cannot consume the
   organisation's whole budget.
7. **Offboarding.** Removing a person's access ends their sessions and invalidates the tile tokens issued to them.
8. **Review.** An independent security review of the above before the first non-developer use.

Until this is done the integration is limited to a **controlled evaluation**: a designated test project, run by named
developers, with the account owner's agreement, with the hard ceilings of section 16.3a in force and nothing retained or
distributed (A-02). This limitation is a gate in section 21.

## 7. Dataset catalogue

### 7.1 Datasets read in this assessment

| Dataset | Earth Engine ID | Read facts | Notes |
|---|---|---|---|
| Sentinel-2 surface reflectance, harmonised | `COPERNICUS/S2_SR_HARMONIZED` | 10/20/60 m bands, stored `uint16`, scale 0.0001; 5-day revisit; 2017-03-28 onward (global coverage in 2017–2018 incomplete); `CLOUDY_PIXEL_PERCENTAGE` is granule-level; QA60 cloud polygons stopped 2022-01-25 and were rebuilt from other masks from 2024-02-28; baseline 04.00 offset handled by the "harmonized" collection (from the 2026-10-29 read, `docs/earth-engine-feasibility.md`; re-check). | Main optical source. Copernicus Sentinel terms apply. |
| Sentinel-2 cloud probability | `COPERNICUS/S2_CLOUD_PROBABILITY` | Band `probability`, 0–100, `UINT8`, 10 m; join to S2 on `system:index` with `ee.Join.saveFirst`; page example masks at ≥ 65. | Older approach. |
| Cloud Score+ for Sentinel-2 | `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED` | Bands `cs`, `cs_cdf` (0–1, 10 m); linked to S2 by `system:index` and `linkCollection`; page: thresholds 0.50–0.65 "generally work well", example `cs_cdf` ≥ 0.60; licence **CC-BY-4.0**; backfill of the full archive was in progress. | Recommended cloud/shadow mask (section 9). |
| Sentinel-1 SAR GRD | `COPERNICUS/S1_GRD` | Thermal noise removal, radiometric calibration, terrain correction with SRTM 30 m (ASTER above 60° latitude), converted to **decibels**; bands `HH`, `HV`, `VV`, `VH` (10 m) and `angle`; 6-day revisit; instrument modes IW, SM, EW; polarisation combinations VV, HH, VV+VH, HH+HV; filter on `instrumentMode`, `transmitterReceiverPolarisation` (and `orbitProperties_pass`) before mosaicking. The raw-power variant is `COPERNICUS/S1_GRD_FLOAT`. | Cloud-independent. Copernicus terms. |
| Landsat 8 Collection 2 Level 2 | `LANDSAT/LC08/C02/T1_L2` | 30 m; from 2013-03-18; SR_B1–B7 scale 0.0000275, offset −0.2; ST_B10 scale 0.00341802, offset 149 (K); QA_PIXEL bits: 1 dilated cloud, 3 cloud, 4 shadow, 5 snow, 6 clear. | Public domain; USGS credit requested. |
| Landsat 9 Collection 2 Level 2 | `LANDSAT/LC09/C02/T1_L2` | As Landsat 8; from 2021-10-31; 16-day revisit; scene about 170 × 183 km; `PROCESSING_LEVEL` is `L2SP` (SR and ST) or `L2SR` (SR only; ST bands present but masked). | Landsat 8 + 9 together give an 8-day cadence [A: arithmetic from the two revisit figures, not a source statement]. |

### 7.2 Candidate datasets not read (verify in the GEE-1 spike before offering)

Sentinel-2 Level-1C (`COPERNICUS/S2_HARMONIZED`), Landsat 7 and 5 Collection 2 Level 2 (long history), Copernicus GLO-30 DEM
(`COPERNICUS/DEM/GLO30`), ESA WorldCover, JRC Global Surface Water, and MODIS products. The identifiers are from general
knowledge, **not** from a page I read, and some have separate licences. None is in scope for the first release unless the client
asks (D-05).

### 7.3 The dataset registry and the availability probe

A static registry in code holds, per dataset: ID, title, sensor, spatial resolution, bands with units and scale/offset, cloud
strategy, licence and attribution text, minimum useful zoom, and `verified_on`. A **probe** (an explicit action, and on first use
per dataset) runs a trivial query (collection size for a one-day window over a tiny box) and records one of
`available`, `not_found`, `permission_denied`, `quota`, `error`, with the time and the dataset's reported date extent. The UI
only offers datasets whose latest probe is `available`. Date limits come from the probe, not from constants (section 3.2).

## 8. Area of interest and date range

1. **Source of the area.** Either the project AOI (EPSG:4326 copy plus `aoi_source` with the as-entered CRS and transformation
   record, [V-code]), or a polygon drawn on the map (EPSG:4326), or the current map view. Earth Engine receives GeoJSON in
   EPSG:4326 (the prototype already does this, [V-code]).
2. **Conversion.** A project AOI is already stored with its Phase 3 transformation record. If the stored record shows an
   *approximate* operation, the search/export request carries that record, and the UI shows its accuracy (for example "area
   located to about 3 m"); nothing is recomputed silently. A new area entered in another CRS goes through `geo/aoi.py` and the
   shared converter exactly as a project AOI does today: ambiguous → 409 with candidates; ballpark-only or grid-missing → refused.
3. **Limits** (starting values, carried over from the prototype and configurable downward): search area ≤ 10,000 km²; export
   area ≤ 100 km² for the immediate path; date range ≤ 366 days per search; no antimeridian crossing; ≥ the dataset's first date;
   not in the future. The batch export path raises the area ceiling (D-06).
4. **Dates** are UTC calendar days, inclusive at both ends; `filterDate` is end-exclusive, so the backend adds one day
   (the prototype already does).
5. **Filters** per dataset: S2 and Landsat – scene cloud percentage (scene-level metadata), sun/viewing limits if offered later;
   S1 – instrument mode (default IW), polarisation (default VV+VH, else the single available), orbit pass (ascending or
   descending, never mixed in one composite).
6. **Scene cloud vs AOI cloud.** The catalogue's cloud figure is for the whole granule, not for the area (the prototype already
   says so, [V-code]). Offer an optional **AOI cloud fraction** computed in Earth Engine from the mask at a coarse scale, shown
   next to the scene figure. It costs compute, so it is computed on request for the listed scenes (maximum 50) and counted by the
   budget (section 16.3).
7. **Search results** are scenes with: ID, acquisition time (UTC), cloud percentage (when present), tile or path/row, platform,
   processing baseline or collection tier, footprint (WGS 84), and the AOI conversion record. The result lists what the
   catalogue says; fields it lacks are `null`, never guessed.

## 9. Cloud filtering and cloud/shadow masking

| Dataset | Default method | Alternatives | Parameters recorded in provenance |
|---|---|---|---|
| Sentinel-2 SR | **Cloud Score+ `cs_cdf` ≥ 0.60** (the page's own example; the page says 0.50–0.65 generally work; higher removes thin cloud, haze and cirrus shadows) | `COPERNICUS/S2_CLOUD_PROBABILITY` with threshold 65 (page example); the scene classification band is not read here and is not proposed. QA60 is **not** proposed: it stopped 2022-01-25 and was reconstructed after 2024-02-28, so it is not consistent over time. | Method, threshold, mask collection ID, the date range for which the mask exists, share of masked pixels in the AOI |
| Landsat 8/9 | **QA_PIXEL**: mask fill (bit 0), dilated cloud (1), cirrus (2), cloud (3), cloud shadow (4); optionally snow (5) | Confidence bits 8–15 for a stricter or looser mask. `QA_RADSAT` to drop saturated pixels. | Bits applied, confidence level used, share masked |
| Sentinel-1 | None (radar sees through cloud) | Speckle filtering (focal median) as an explicit option, not a default; border-noise handling | Whether speckle filtering was applied and with what radius |

Rules: a mask is always **visible and removable** in the layer builder (a "show masked pixels" toggle); a masked pixel becomes
transparent in tiles and a stated nodata in exports (never zero); the fraction of the AOI masked is reported; a composite also
offers a per-pixel *valid observation count* so thin data is visible. No mask is applied to a single-scene export unless the
person selects it (matching today's "no cloud masking" statement in the export record).

## 10. True-colour and false-colour visualisation

Visualisation is applied by Earth Engine for remote tiles and recorded as parameters; it never alters stored pixel values. For
exports, values are exported **unstretched** (the visualisation is a separate, optional 8-bit preview export).

| Product | Sentinel-2 | Landsat 8/9 | Sentinel-1 |
|---|---|---|---|
| True colour | B4, B3, B2 | SR_B4, SR_B3, SR_B2 | — |
| Colour infrared (vegetation) | B8, B4, B3 | SR_B5, SR_B4, SR_B3 | — |
| SWIR composite (moisture, burn scars) | B12, B8, B4 | SR_B7, SR_B5, SR_B4 | — |
| SAR | — | — | VV; VV, VH, VV/VH ratio |

Band combinations are standard remote-sensing practice [A], not text from the pages read. Stretch defaults (`min`, `max`,
`gamma`) are initial values for the viewer: S2 reflectance stretched about 0–0.3, Landsat reflectance about 0–0.3 after scale and
offset, S1 about −25 to 0 dB [A]. They are tuned in GEE-3 and always shown and editable. The Earth Engine parameters used are those
in [V16]; palettes apply to single-band products (indices). Each visualisation carries a legend: band names, stretch, units, the
dataset, the date range and the mask.

Landsat bands must be scaled with scale 0.0000275 and offset −0.2 **before** any display or index (the offset means the factor
alone is wrong) [V-doc]. Sentinel-2 values are digital numbers with scale 0.0001 [V-doc].

## 11. Analysis workflows (indices)

Definitions are the standard ones [A]; each is stored in provenance with the band mapping and formula text, so the result can be
reproduced by hand.

| Index | Formula | Sentinel-2 | Landsat 8/9 | Notes |
|---|---|---|---|---|
| NDVI | (NIR − Red) / (NIR + Red) | B8, B4 | SR_B5, SR_B4 (after scale and offset) | Vegetation vigour. Range −1…1. |
| NDWI (McFeeters) | (Green − NIR) / (Green + NIR) | B3, B8 | SR_B3, SR_B5 | Open water. |
| NDWI (Gao) | (NIR − SWIR1) / (NIR + SWIR1) | B8, B11 | SR_B5, SR_B6 | Vegetation water content. **Two indices share the name NDWI; the UI shows which one.** |
| MNDWI | (Green − SWIR1) / (Green + SWIR1) | B3, B11 | SR_B3, SR_B6 | Water in built-up areas. Optional. |
| NDBI | (SWIR1 − NIR) / (SWIR1 + NIR) | B11, B8 | SR_B6, SR_B5 | Built-up. Noisy where bare soil is present; not a classifier. |
| NBR | (NIR − SWIR2) / (NIR + SWIR2) | B8, B12 | SR_B5, SR_B7 | Burn severity with a before/after pair. Optional. |
| EVI | 2.5 (NIR − Red) / (NIR + 6 Red − 7.5 Blue + 1) | B8, B4, B2 | SR_B5, SR_B4, SR_B2 | Optional. Needs reflectance units. |
| SAR ratio / change | VH − VV (dB), difference of two dates | — | — | Flood and change screening. Averaging in dB is not physically linear; averaging in linear power uses `S1_GRD_FLOAT` [V-doc for the existence of that product]. |

Notes:
- Sentinel-2's SWIR bands are 20 m, NIR is 10 m. An index mixing them is computed on a stated grid (10 m by resampling, or 20 m
  natively). The prototype already refuses a scale finer than the coarsest band requested [V-code]; the same rule applies.
- Indices are floating point, −1…1. Exports are `float32` with an explicit nodata. The existing ingestion and display code already
  handle float rasters [V-code: `kinds <= {"u","i","f"}`].
- An index map is a **screening aid**; it is labelled as such. The application makes no land-cover or damage claim from an index.
- Threshold-based products (for example a water mask from NDWI > 0) are not offered in the first release; they belong to the
  analysis phase after the client defines acceptance criteria (D-12).

## 12. Historical imagery and temporal comparison

- **Timeline.** A date-range selector with a per-sensor availability strip (earliest date from the probe), and a list of scenes
  or composite windows. Sentinel-2 history starts 2017-03-28; Landsat 8 in 2013; Landsat 9 in 2021 [V-doc]; Landsat 5/7 for
  earlier years only if asked (D-05).
- **Single scene vs composite.** A single scene is exact and reproducible by ID. A **composite** (median over a date window, after
  masking) fills gaps but is a derived product: the recipe records the window, the mask, the reducer and the number of valid
  observations per pixel. Compositing across different sensors is not offered (S2 vs Landsat differ in bands, resolution and
  calibration; no harmonisation is claimed).
- **Before/after.** A swipe or side-by-side view of two recipes (two dates) on one map. For analysis, both dates are exported on
  the **same grid** (same `crs` and `crsTransform`, V15), so the existing comparison tool, which requires identical CRS and does not
  reproject [V-code, `docs/crs-conversion-coverage.md` row 22], can be used unchanged on the two local assets. Differencing
  remains local, auditable, and reproducible from stored originals; Earth Engine-side difference layers are previews only.
- **Time series.** A chart of an index's mean (and valid-pixel fraction) over the AOI per scene or per month, computed by Earth
  Engine on request and counted by the budget; the numbers are shown with their mask and scale. It is a remote result, saved only
  as a CSV if the person exports it, with provenance.
- **Date semantics.** Acquisition time is UTC from `system:time_start`. Local-time display is formatting only.
- **Reprocessing.** Providers reprocess archives; a recipe pins scene IDs and the dataset's reported processing version so a later
  difference can be explained (risk R-11).

## 13. Earth Engine map tile integration with the existing map workspace

### 13.1 Flow

1. The person builds a **recipe** in the layer builder. The backend validates it against the dataset registry and the limits
   (section 16), builds the Earth Engine image expression, and calls `getMapId` with the visualisation parameters
   [V16, function named in the Earth Engine image documentation].
2. The backend stores the recipe (and, held in memory only, the upstream tile address and token) and returns a **layer
   descriptor** to the browser: an opaque layer ID, a title, bounds, min/max zoom, a legend, attribution, and a tile URL of the
   form `/api/v1/remote-layers/{layer_id}/tiles/{z}/{x}/{y}.png?v={recipe_hash}`.
3. MapLibre requests tiles from that URL only. For **every** request the proxy performs the project-scoped authorisation of
   section 13.3, checks that the layer exists, belongs to a project with `allow_online`, is within budget (a reservation, section
   16.3a), and the kill switch is off, and only then fetches the matching tile from Earth Engine and streams it with
   `Cache-Control: no-store`.
4. If Earth Engine answers 401/403/404 (token or map expired), the backend re-creates the map **once** from the stored recipe and
   retries; a second failure returns a 502 with a stable error code. Map ID lifetime is unknown [X].
5. A 429 from Earth Engine is passed on as `Retry-After`, and the client library's own five retries with exponential backoff
   [V13] are not stacked on top of a second retry layer.

### 13.2 Why a proxy

- Satisfies "never expose credentials in the browser" and "do not make private imagery or tiles publicly accessible". A raw
  `getMapId` URL carries its own token; if the browser received it, anyone who saw it could fetch tiles at the organisation's
  expense for as long as it is valid.
- Gives one place to enforce budgets, concurrency, per-project opt-in and audit.
- Keeps the map style free of third-party sources, as it is today (decision log P4-10: the style references no online source,
  glyphs or sprites).
- The proxy **must only contact the host of the upstream tile address returned by Earth Engine**, checked against an allow-list
  of Google domains, to prevent it being used as a request forwarder (SSRF).

### 13.3 Project-scoped authorisation of every remote tile request (mandatory)

The local tile route needs no profile header [V-code], which suits local files but not a route that spends money and exposes
imagery chosen for a particular project. **Project-scoped authorisation is mandatory for every remote tile request, with no
exceptions and no "trusted" fast path.** A tile token must never give access to another project's layers.

1. **The token is a capability for exactly one layer in one project.** A short-lived (minutes), HMAC-signed query parameter
   issued by the layer-descriptor call. Its signed payload contains: `layer_id`, `project_id`, the authenticated user's identifier
   and session (section 6.6), the recipe hash, an expiry and a random identifier. The signing secret is an application secret held
   in settings (never in the browser, never a Google credential) and can be rotated; rotation invalidates all outstanding tokens.
2. **Every request is re-authorised on the server**, not just at issue time. For each tile the server: verifies the signature and
   expiry; checks that the `layer_id` in the **URL path** equals the token's `layer_id` and that the layer's stored `project_id`
   equals the token's `project_id` (a token issued for layer A in project X is refused for layer B, and for any layer of project
   Y, even if the caller knows the other layer's ID); checks that the token's user is still a member of that project with a role
   that permits viewing (the live check, so offboarding or role removal takes effect immediately); checks that the project is
   not archived and is still `allow_online`; and checks the recipe hash matches the layer's current recipe.
3. **Unguessable identifiers, no enumeration.** Layer IDs are random and unique; a request for a missing layer, a layer of
   another project, or with a bad token returns the same response (404 or 403 with no detail) so that existence is not revealed.
4. **No cross-project reuse of upstream state.** The upstream Earth Engine address and token are held per layer and keyed by
   `(project_id, layer_id)`; they are never shared between layers or projects and are never returned.
5. **Concurrency and cost.** A per-layer and global concurrency cap (below Earth Engine's 40 concurrent-request default so one
   pan does not starve other users, [V13]); each tile fetch is covered by a usage reservation (section 16.3a); an immediate stop
   when the kill switch is on or a ceiling is reached.
6. **Why a signed parameter.** Requests from `<img>`/MapLibre cannot carry the profile or session header, which is why the
   capability is in the URL; because it is in the URL it is short-lived, scoped as above, never logged in full (redaction), and
   is revalidated against live authorisation on each use.
7. **Interim limitation.** Until real user authentication and project membership exist (section 6.6), per-user binding is not
   possible. In that state the tile proxy may serve only a designated test project to named developers, and no claim of
   cross-user isolation is made.
8. **Tests (section 22).** A token for project X refused on project Y's layer; a token for layer A refused on layer B of the same
   project; an expired, tampered or wrong-recipe token refused; a user removed from the project refused immediately; an archived
   or `local_only` project refused; no difference in response between "no such layer" and "not yours".

### 13.4 Caching

Caching of Earth Engine tiles would cut cost but the Terms I read do not address it [V-doc gap]. Until the organisation
confirms in writing (A-02): **no persistent tile caching of any kind**: no disk or database cache on the server, and no
browser-persisted cache (responses carry `Cache-Control: no-store`). The proxy may hold bytes in memory only for the duration of
a single in-flight request, to answer identical simultaneous requests; nothing survives the request. If caching is approved later, the key is the recipe hash plus
`z/x/y` (a new recipe is a new URL, as with the local `&operation=` and georeferencing version in the key today).

### 13.5 Map and layer panel behaviour

- `LayersOut` gains a separate array `remote_layers` (additive; existing consumers are unaffected).
- Remote layers appear in their own group in the layer panel with a "Remote" badge; opacity and visibility work as for rasters.
  They do not appear in the raster pixel inspector, statistics, terrain, georeferencing or control-point tools, and are not
  listed in Assets.
- The map's footprint outlines for remote layers are drawn from the scene geometry in WGS 84 [A: Earth Engine provides scene
  geometry as GeoJSON in WGS 84].
- Remote layer disabled state when Earth Engine is off, over budget, or the project is `local_only`: the group shows the reason;
  nothing is requested.
- Tile CRS: both the local tiles and Earth Engine map tiles are drawn in Web Mercator for display [A-14 for Earth Engine].
  Display projection is not a CRS decision for the data (it is how `tiles.WEB_MERCATOR` already works for local rasters).

## 14. Asynchronous export tasks and GeoTIFF ingestion

### 14.1 Export paths

| Path | Limit (documented or carried over) | Use |
|---|---|---|
| `getDownloadURL` | 32 MB, 10,000 px per side (earlier research, not re-confirmed) → the prototype caps at 16 MiB, 1024 px, 4 bands, 100 km² | Small areas; no bucket needed; returns within seconds; the download URL is itself a short-lived credential and is never logged. |
| **Batch export to Cloud Storage** (`Export.image.toCloudStorage`, GeoTIFF, optionally COG) | Task queue 3,000 READY; commercial concurrency by plan [V13]; large exports split into tiles named `baseFilename-yMin-xMin` [V15] | Larger areas and more bands. Needs a billed project, a bucket, write permission on it [V15]. |
| Export to Drive | Not supported under VPC Service Controls [V15]; writes into a user's Drive | **Not used.** A personal Drive is not a safe server-side channel. |
| Export to Earth Engine asset | Incurs storage charges even if the API is disabled [V3] | Not used. |

The batch path is the substantive new capability; it needs a **dedicated private bucket** with uniform bucket-level access,
public access prevention, a one-day (or similar) lifecycle delete, and an access list of the service identity only. The backend
downloads the object, verifies it, ingests it, then deletes the bucket copy.

### 14.2 Job design

New job kind `ee_export` on the existing queue [V-code] (needs `ck_job_kind` widened, section 19):

1. `validating` – re-validate the recipe, re-check budget and dataset probe, convert the AOI through the planner, build the
   export plan (the prototype's `plan_export` pattern), create the `ee_export` record.
2. `processing / submitted` – start the Earth Engine task (or compute the `getDownloadURL`); store the task ID.
3. `processing / running` – poll with back-off; update progress (Earth Engine reports states, not a percentage, so `progress` stays
   null for a batch export, which the schema already allows [V-code: "null when not measurable"]); honour `cancel_requested` by
   cancelling the Earth Engine task.
4. `processing / downloading` – stream to a `.part` file in a scratch folder with the byte ceiling; compute SHA-256.
5. `processing / verifying` – the prototype's `_inspect` checks (georeferenced, CRS equals requested, grid within tolerance, band
   count, size) and, for multi-tile outputs, refusal or a documented mosaic step (D-06).
6. `processing / ingesting` – call `ingest()` with modality `overhead_raster`, origin `online_import`, then write the provenance
   record and the export record in the **same transaction** as the asset row; the file is committed to storage after, as ingestion
   already does. A failed attempt runs the `discard` actions so nothing is left behind [V-code `JobContext.discard`].
7. `completed` – job result holds the asset ID and a pointer to the provenance record.

Errors: authentication, permission, quota-exceeded and "dataset not found" are permanent (`JobError`); 429, 5xx and network
timeouts are transient (`TransientJobError`, retried with the existing back-off); bucket object not found after a "succeeded"
task is permanent.

### 14.3 What ingestion does with the file

Unchanged. The file's own CRS (a WGS 84 UTM zone) and geotransform make it a trusted georeferenced raster; its footprint is
computed through the planner (exact, because UTM zone → WGS 84 does not change the datum); tiles are drawn from the file; the
stored original is byte-identical to the delivered export and its SHA-256 equals the one in the provenance record, as the prototype
tests already prove with a stand-in file [V-code, `tests/test_satellite.py`]. Band descriptions are kept; Earth Engine band names
are preserved as band descriptions if the delivered file carries them (to be confirmed in the spike).

### 14.4 Storage and cleanup

Exports land first in a scratch folder with the `_satellite` prefix convention [V-code] and are never written inside `originals`,
`blobs` or `previews`. Bucket objects are deleted after a verified download; the lifecycle rule is the backstop. Disk reserve
checks (`storage_reserve_bytes`) apply before download.

**Retention is gated by the commercial terms (A-02).** Ingesting an export as a stored asset keeps imagery indefinitely and makes
it available to the client. Until the organisation has approved, in writing, the Google Cloud commercial terms for retaining
exported imagery, exports made during an evaluation are verified and then **deleted** (the scratch file, the bucket object, and
any ingested test asset in a scratch project); they are not added to any working project, delivered to the client, or included
in a report. Data-licence obligations of the dataset (Sentinel, Landsat, Cloud Score+; section 16.5) are separate and also apply.

## 15. CRS compatibility with the Phase 1 and Phase 3 transformation policies

| Case | Behaviour |
|---|---|
| Project AOI in a non-WGS 84 CRS, sent to Earth Engine | Already converted and stored in EPSG:4326 at project creation through the shared converter with its provenance record ([V-code]). A new area is converted the same way. Ambiguous → 409 with candidates; ballpark-only → refused; missing grid → refused naming it. The accuracy of the shift used is shown beside the area and written in the export provenance. |
| A request with the area given as coordinates in a CRS other than 4326 | Converted with `geo.convert` before anything is built; `operation` accepted as in the other endpoints (`transformation_operation`). |
| Export CRS | Chosen by the backend or the person from an **allow-list of WGS 84 UTM zones** (default: zone of the AOI centre, as the prototype does); optionally a project-appropriate registered CRS. The CRS is passed to Earth Engine explicitly as an EPSG string, never left to `scale` alone (V15 warns the grid can shift). A CRS outside the registry is refused; the application does not define CRSs for this. |
| An AOI that straddles a UTM zone boundary or lies in a polar region | Warn (the prototype range check refuses polar areas); the person picks a zone or accepts the centre zone. No silent reprojection to a different datum. |
| Ingested export placed on the map | Uses the normal tile path: UTM (WGS 84) → Web Mercator is the *planner*'s "datum-unchanged" exact case. |
| Overlaying an export on a map-sheet or raster in another datum (for example an older regional datum) | Handled by the existing alignment and layer ordering tools; the Phase 3 policy applies unchanged; nothing is claimed about the offset between datums beyond the registered operations' stated accuracy. |
| Remote layers (tiles) | Earth Engine renders them; the application makes **no coordinate conversion** for them beyond mapping the display's Web Mercator address to the proxy URL. They have no pixel lookup or measurement of pixel values (section 5.3); the point-click readout for a remote layer, if added, is a WGS 84 point sent to Earth Engine and returned with the value and the dataset identifier. |
| Distance and area on remote layers | Existing WGS 84 geodesic tools on map points; they do not depend on the layer. |
| 3D terrain | Not affected. Elevation datasets from Earth Engine (for example GLO-30) are not in scope; no vertical datum is assumed anywhere. |
| ESM / Everest-56 / Polyconic | Nothing here defines or supports them. Earth Engine data is in WGS 84-based CRSs. No claim of compatibility with ESM sheets is made; overlay on such sheets is governed by the existing alignment tools. |
| Guard test | `test_transform_consistency.py` forbids direct PROJ transformers outside a reviewed list. The Earth Engine modules add no entry to that list; any conversion goes through `geo.convert`. |
| What the new code may do with PROJ | Nothing directly. Computing the UTM zone code from a longitude is arithmetic (as `utm_crs_for` already does), not a transformation. |

## 16. Storage, security, billing and usage controls

### 16.1 Storage

| Item | Where | Notes |
|---|---|---|
| Exported GeoTIFF originals | Existing blob store | Byte-identical, SHA-256 keyed, never modified. |
| Provenance | New `ee_export` row plus the delivered provenance JSON (section 19) | Machine-readable; shown on the asset page. |
| Recipes | Memory (GEE-3) → database (GEE-4+) | Immutable once created. |
| Upstream map tokens | Memory only | Never persisted, never logged. |
| Bucket copies | Private bucket, lifecycle delete | Deleted after verified download. |
| Local disk | Scratch folder under the data volume, with a reserve check | The existing 1 GB free-space reserve applies. |

### 16.2 Security controls

See section 23 for the risk register. Controls in summary: backend-only credentials; no Earth Engine value in any response
except tile bytes and descriptors; proxy host allow-list; signed short-lived tile parameter; per-project opt-in; kill switch;
budgets; redaction; least-privilege identity; private bucket; downloaded files treated as untrusted input (size cap before
reading, ingestion validation, no execution); audit events for every outbound action.

### 16.3 Usage controls and cost controls

| Control | Mechanism | Evidence |
|---|---|---|
| Hard off-switches | Both existing switches (`GEOPOC_ONLINE_ENABLED`, `GEOPOC_EARTH_ENGINE_ENABLED`) plus a runtime **kill switch** (a flag file outside the repository, checked on every request, because settings are cached at start-up) | [V-code] |
| Per-project opt-in | `project.source_preference = allow_online` | [V-code] |
| Request ceilings | AOI area, date span, bands, pixels, bytes, scenes (section 8), enforced **before** any call | [V-code] pattern |
| Concurrency | Backend semaphore for interactive calls (below 40) and for batch tasks (below the plan's limit) | [V13] |
| Rate limits | Per profile and global, for tiles, searches, exports | [A] values set in GEE-6 |
| Reservations, ledger and hard ceilings | Every costly operation reserves before it runs and is refused if the reservation would pass a ceiling; actual usage recorded afterwards. Persistent atomic ledger (Migration L, separately approved) for multi-user, multi-worker or production operation; in-process reservation and local usage log in the controlled evaluation model. **Mandatory requirements and operating models in section 16.3a.** | design |
| Cloud-side quota | Ask the administrator to set the optional per-day EECU-time quota, and a concurrency limit | [V13]; the page calls the quota approximate, so it is a backstop |
| Billing alerts | Cloud Billing budget with alert thresholds | [A] standard Cloud feature; alerts notify, they do not stop spending by themselves |
| Monitoring | Cloud Monitoring on `earthengine.googleapis.com/daily_eecu_usage_time` with an alert | [V14] |
| Cost visibility | Each layer descriptor and export dialog shows "this will send the area and date range to Google" and a cost class (preview / small export / batch export), with measured EECU after the first approved test | design |
| Measuring real cost | The first approved spike records EECU and charges per tile, per search and per export for representative areas, before limits are tuned | [X] |

### 16.3a Mandatory budget-control requirements

These are requirements, not options. A phase that spends Earth Engine compute or storage may not exit until each is met and
tested **for the operating model it runs in** (see "Operating models" at the end of this section). They apply to every
spending path: tile requests, searches, AOI-cloud fractions, time series, point values, interactive computations in the spike,
and export tasks. Items 1 and 2 are written for operating model B (multi-user, multi-worker or production), where the state
must be persistent; operating model A (the controlled evaluation model) meets the same guarantees by restricting the
deployment to a single process, as defined below. Items 3 to 6 apply to both.

1. **Atomic usage reservations.**
   - Before any operation that can incur cost, the server **reserves** its estimated cost against the ledger in a single atomic
     database step (a conditional update or a row lock on the budget row inside a transaction): "add this reservation only if
     committed usage + outstanding reservations + this estimate stays within every applicable ceiling." Check-then-act in
     application code is not acceptable, because two requests can both pass the check.
   - If the reservation fails, the operation is **not started** and the caller receives `EE_BUDGET_EXCEEDED` naming the ceiling
     that was hit. If the ledger cannot be read or written, the operation is refused (**fail closed**).
   - Reservations carry an owner (user, project, job or request id), an expiry, and a unit (EECU estimate, tile requests,
     bytes, tasks). Expired reservations are released by a sweeper so a crashed process cannot lock budget forever.
   - Tile traffic reserves in small leases (for example a block of tile units per layer per interval) to avoid a database round
     trip per tile; the lease cannot exceed the remaining ceiling, and an unused part is released.
2. **Concurrent worker protection.**
   - The reservation mechanism is correct across **several API processes and several job workers** at once, because its state is
     in the database and updated atomically; process-local counters or locks are not sufficient and are not relied on.
   - Export tasks are limited by a database-enforced cap on concurrent tasks per project and globally, claimed in the same
     transaction as the reservation, so two workers cannot both start the "last" slot.
   - Retries (`TransientJobError`) reuse the job's existing reservation, or release and re-reserve; they never reserve twice for
     one logical attempt, and a cancelled or failed job releases its unused reservation.
   - Concurrency tests (section 22) run many simultaneous requests and workers against a small ceiling and assert the ceiling is
     never exceeded.
3. **Recorded actual usage.**
   - Each completed or failed operation writes a ledger row with the estimate, the actual (or best available) cost, who and
     what caused it (authenticated user, project, layer or job), the dataset, the area and pixel counts, the outcome and time.
   - Where Earth Engine returns no per-request EECU figure [X, to be established in the spike], the recorded actual is the
     conservative estimate and is marked as an estimate; a daily reconciliation compares the ledger total with the Cloud-side
     daily usage metric (`earthengine.googleapis.com/daily_eecu_usage_time`, [V14]) and the billing data, and an unexplained
     shortfall tightens the ceilings automatically and alerts the administrator.
   - Ledger rows are append-only (no update or delete), so the history cannot be edited after the fact.
4. **Hard server-side spending ceilings.**
   - Ceilings are enforced **by the server**, at reservation time, at these levels at least: per request; per user per day;
     per project per day and per month; global per day and per month; maximum concurrent interactive requests and batch tasks.
   - A ceiling cannot be raised by a request parameter or from the browser. It changes only by an administrator action
     recorded in the audit history, within an upper bound set in server configuration by the account owner.
   - The application's ceilings are set **below** the Cloud-side controls (per-day EECU-time quota, concurrency, billing
     budget), so the application stops first; the Cloud-side controls are a backstop, not the control [V13: the quota is
     "approximate"; billing budgets alert and do not stop spending by themselves, [A]].
   - Hitting a ceiling disables only the operation that would exceed it and tells the person which ceiling and when it resets.
5. **Global kill switch.** See 16.4.
6. **Visibility.** The administrator can see, at any time, committed usage, outstanding reservations, remaining budget at each
   level, the largest consumers, and every refusal; alerts fire at configurable thresholds (for example 50, 80, 100 percent).

#### Operating models

Requirements 1 and 2 need persistent, shared state when more than one process or person is involved. That state needs a
database migration (Migration L, section 19). Migration L is a **proposal only**: it is **not applied or assumed by this
document**, and it needs separate approval, a backup and scratch-database testing first. What it gates is the *operating
model*, not a particular phase number.

| | **Operating model A: controlled evaluation** | **Operating model B: multi-user, multi-worker or production** |
|---|---|---|
| Who | The account owner and named developers only; one designated test project | Authenticated users with roles (section 6.6) |
| Processes | **One** API process and **at most one** job worker doing Earth Engine work, on one host | Several API processes and job workers |
| Reservation and ceilings | In-process reservation guarded by a process-wide lock, so check-and-reserve is atomic within the process; fails closed; hard ceilings fixed in server configuration by the owner (low values); concurrency limits for interactive calls and batch tasks | Persistent atomic reservations in the database (Migration L), per-user, per-project and global ceilings |
| Single-instance guarantee | An exclusive single-instance guard (a lock held outside the database and the repository) makes every Earth Engine path **refuse to run** if a second API process or worker would also use Earth Engine | Not needed; the database provides it |
| Usage record | Append-only local usage log file kept outside the database and the repository, replayed at start-up to restore the day's totals; Earth Engine refuses to run if the log cannot be read or written. An evaluation aid, not the audit record of record | `ee_usage_event` ledger (append-only) |
| Backstops | Cloud-side per-day EECU-time quota, concurrency limit and billing alert; global kill switch (16.4) | Same, plus reconciliation with Cloud usage |
| Database change needed | **None** for budget control. (Migration E for exports, GEE-4, is independent and does not lift these limits) | Migration L, separately approved and applied |
| Not permitted | Any non-developer use; more than one process or worker; production use | n/a |
| Required before use | G-K (kill switch), the A-model conditions in this table, and every restriction in section 26 | G-B (Migration L applied and 16.3a tested under concurrency), G-U, and G-T for distribution and retention |

**Rules that follow.**

1. **GEE-1** is a manual, owner-run evaluation: no database migration, no tile proxy, scene-search service or export job
   exposed to anyone, every call made by hand under the Cloud-side quota and billing alert (section 21).
2. **GEE-2 and GEE-3 must remain within operating model A if Migration L has not been approved and implemented.** They may not
   serve a second user, run on more than one process or worker, or be treated as production, whatever their other progress.
3. **Leaving operating model A** (adding a user, a worker, or production use) requires Migration L to be separately approved
   and applied, G-B demonstrated by concurrency tests, and the other gates that apply (G-U, G-T).
4. **Nothing in operating model A weakens** the commercial-terms, security, AOI-transmission, caching, CRS or data-preservation
   restrictions: A-02 and gate G-T, the project-scoped authorisation of every tile (13.3), explicit-action-only transmission of
   an area (5.1), no caching of any kind (13.4), the shared converter (15) and the preservation of existing data and originals
   (section 26) apply in full to both models.
5. The in-process lock and local log are **not** a substitute for the persistent ledger: they protect one process, they can be
   bypassed by running a second one on another host, and they are lost with the machine. They are accepted only because the
   model is limited to named people on one host, under Cloud-side limits.

### 16.4 Global kill switch and incident handling

- **One switch stops everything.** A global kill switch, held outside the application database and outside the repository (a
  flag file or equivalent, plus the existing `GEOPOC_EARTH_ENGINE_ENABLED` setting), is checked **on every Earth Engine request,
  tile and job step**, because settings are cached at start-up. It needs no restart and no database access, so it works when the
  ledger or the database is unavailable.
- **Fail closed.** If the switch cannot be read, Earth Engine is treated as off.
- **Effect.** Every Earth Engine endpoint returns `EE_DISABLED`; the tile proxy stops serving; queued Earth Engine jobs are held,
  not started; running exports are cancelled at the next poll (`cancel_requested`) and their Earth Engine tasks cancelled;
  outstanding reservations are released. Local data, local rasters and every non-Earth-Engine feature are unaffected.
- **Who and how.** Operable by the Earth Engine administrator role and by anyone with host access; the action, actor and time
  are written to the audit history where possible, and to the log if the database is unavailable. Turning it back on is a
  separate deliberate action, never automatic.
- **Test.** The switch is exercised in the automated suite and in a documented live drill during GEE-6 and GEE-7.
- **Beyond the application.** An administrator can disable the Earth Engine API for the Cloud project to stop compute charges
  [V3]. On suspected credential exposure: engage the kill switch, disable the identity, revoke any key, rotate, review the Cloud
  audit logs.

### 16.5 Licence, attribution and provenance

Sentinel data: "Contains modified Copernicus Sentinel data [year]" (the prototype's text, to be confirmed by A-11). Landsat:
USGS credit requested. Cloud Score+: CC-BY-4.0 attribution to Google. The service line: imagery retrieved through Google Earth
Engine. The attribution string is part of every layer descriptor (shown in the map corner) and every export record. A report or
export that includes a remote layer's picture repeats it. Synthetic (mock) data is never given a Copernicus credit [V-code].

## 17. API design (proposed; additive, `/api/v1`)

All errors use the existing problem format. Earth Engine-specific codes: `EE_DISABLED`, `EE_NOT_CONFIGURED`,
`EE_AUTH_FAILED`, `EE_PROJECT_LOCAL_ONLY`, `EE_DATASET_UNAVAILABLE`, `EE_REQUEST_TOO_LARGE`, `EE_QUOTA`, `EE_BUDGET_EXCEEDED`,
`EE_UPSTREAM`. Transformation errors are the existing `TRANSFORMATION_*` codes.

| Method and path | Purpose |
|---|---|
| `GET /earth-engine/status` | Enabled flags, auth mode, connection state, last check, kill-switch state, configured limits. No secret. |
| `POST /earth-engine/connection-test` | Explicit, rate-limited initialisation test. |
| `GET /earth-engine/datasets` | Registry with bands, scale, licence, attribution, and the latest probe result per dataset. |
| `POST /earth-engine/datasets/{id}/probe` | Run the availability probe. |
| `POST /projects/{pid}/earth-engine/scene-searches` | Body: dataset, `aoi` (`use_project_aoi` or `{geometry, crs_id}`), `transformation_operation`, dates, cloud limit, dataset filters. Returns scenes with footprints, and the AOI conversion record. 409 for selection required; 403 `EE_PROJECT_LOCAL_ONLY`. |
| `POST /projects/{pid}/remote-layers` | Create from a recipe. Returns the descriptor. |
| `GET /projects/{pid}/remote-layers`, `GET/DELETE /remote-layers/{id}` | List, read, remove (removal never touches local assets). |
| `GET /remote-layers/{id}/tiles/{z}/{x}/{y}.png?sig=…&v=…` | Tile proxy. Authorised against the layer's project on every request; the token is bound to one layer in one project (section 13.3). `Cache-Control: no-store`. |
| `GET /remote-layers/{id}/point?lon=&lat=` | Optional later: value at a WGS 84 point, labelled remote. |
| `POST /remote-layers/{id}/exports` | Create an export job from the recipe (or from a scene); returns 202 and the job. Includes `crs_id` (allow-list), `scale_m`, `bands`, `masked`. |
| `GET /jobs/{id}` (existing) | Progress, events, result with the new asset ID. |
| `GET /earth-engine/usage` | Ledger totals and remaining budget. |
| `GET /assets/{id}` (existing, additive) | Gains `source` block when `origin = online_import`: provider, dataset, scene IDs, recipe hash, attribution, pointer to the provenance record. |

**Recipe (the unit of reproducibility)** – immutable JSON: dataset ID, selection (scene IDs, or date window plus reducer),
filters, mask (method, threshold, mask dataset), index or band composite, visualisation (bands, min, max, gamma, palette),
AOI (EPSG:4326 geometry and its conversion record), requested scale, and the registry `verified_on`. Its SHA-256 is the layer
version in tile URLs. A recipe never contains a credential.

## 18. User interface design

1. **Settings → Earth Engine** (new tab): status (off / not configured / connected / error), auth mode, project id (maintainers
   only), switches shown as read-only facts (set by the account owner outside the app), dataset availability table with a Probe
   button, usage and limits, and the kill switch state. No credential entry fields; the page says where credentials live.
2. **Project → Imagery** (new screen, route `/projects/:id/imagery`; not `/satellite`, which exists): left panel – dataset,
   area (project AOI, drawn polygon or map view), dates, cloud limit, dataset filters, Search; centre – the map with scene
   footprints; right – result list (date, platform, cloud, tile) and scene detail with the AOI-conversion note. A banner states
   plainly when the project is `local_only` and offers no outbound action.
3. **Layer builder** – a stepped panel: choose scenes or a date window; choose mask (default on, with the threshold and a "show
   masked" toggle); choose product (true colour, colour infrared, SWIR, SAR, or an index with palette); live legend; a text line
   "These pixels are computed by Google Earth Engine and are not stored here."; Add to map.
4. **Map workspace** – remote group with badge, opacity, visibility, remove, legend and attribution; no pixel inspector for
   remote layers (a short explanation appears if the person tries); a **Save as local raster** button opens the export dialog.
5. **Export dialog** – shows area, pixel size, bands, CRS (allow-list, default stated), estimated size and path (immediate vs
   batch), cost class, the data-leaves-the-machine notice, and the attribution; then a job tracked in the existing jobs UI.
6. **Asset page** – a "From Earth Engine" block with dataset, scene, dates, mask, formula, CRS, grid, attribution, the recipe hash,
   and the SHA-256 link to the provenance record.
7. **States and wording**: Earth Engine off, not configured, auth failed, over budget, dataset unavailable, transformation
   selection required (reuse the existing chooser component), request too large (with what to change). Plain language; no raw
   exception text; keyboard and screen-reader support following the current design system and tests.
8. **Reports and exports** list remote layers as "remote preview (not stored)" with attribution when shown; findings and
   annotations stay attributed to local rasters.

## 19. Database changes (proposal; none applied; separate approval and a backup first)

**No migration is written, applied or assumed by this document; every migration below needs its own separate approval, a
backup first, and scratch-database testing.** They fall in two groups:

- **Migration L (ledger), a proposal that needs separate approval; required before multi-user, multi-worker or production
  budget-controlled operation (operating model B, section 16.3a), not automatically before GEE-2.** The usage ledger and
  reservations required by section 16.3a for that model, which cannot be correct under concurrency in memory. Until Migration L
  is approved and applied, GEE-2 and GEE-3 run only in the controlled evaluation model (operating model A), which needs **no
  database change**; GEE-1 is a manual, owner-run evaluation with no migration. Recipes can be held in memory in GEE-2 and
  GEE-3, and the project gate uses the existing `source_preference` column.
- **Migration E (exports), GEE-4:**

| Change | Why | Evidence |
|---|---|---|
| Widen `ck_asset_origin` to include `online_import` | Mark exported imagery as such instead of mislabelling it `upload` | [V-code] currently `('upload','derived')` |
| Widen `ck_job_kind` to include `ee_export` (and `ee_probe` if probes are jobs) | New job type | [V-code] |
| New `ee_remote_layer` (id, project_id, recipe JSONB, recipe_sha256, created_by, created_at, archived_at) | Persist recipes for reproducibility and for reloading a project's layers | design; append-only |
| New `ee_export` (id, project_id, job_id, asset_id nullable, recipe_sha256, request JSONB, ee_task_id, status, provider/project metadata, file sha256, bytes, crs, grid, aoi_conversion JSONB, attribution JSONB, timestamps, actor) | Provenance queryable from the database | design; the lineage table cannot hold a remote parent |
| **Migration L:** New `ee_budget` (scope, scope_id, period, ceiling, committed, reserved, updated_at; updated only by atomic conditional statements), `ee_usage_reservation` (id, scope keys, owner, unit, estimate, expires_at, state: held / committed / released / expired) and `ee_usage_event` (id, at, authenticated actor, project_id, layer or job id, dataset, kind, unit, estimate, actual, actual_is_estimate, outcome, detail JSONB; append-only) | Atomic reservations, concurrent-worker protection, recorded actual usage and hard ceilings (section 16.3a) | design; the event table is append-only by trigger; `ee_budget` is the only mutable table and is changed only through the reservation functions |
| Optional: `asset.source_info JSONB` (named in the original blueprint) | A place on the asset for source, retrieval time, identifier, licence | [V-code blueprint]; alternative: use `ee_export` only |
| Triggers | Append-only on recipes and usage events, as other history tables | existing pattern |

Compatibility: all additive; no existing table loses data; the checks are widened, not narrowed; downgrade drops the new tables
(after exporting rows) and narrows the checks only if no row uses the new values. Tested first on the scratch databases with the
migration-chain, downgrade and constraint tests, as for earlier migrations. Existing projects, rasters, georeferencing,
transformation choices and AOIs are not touched.

## 20. Dependencies

| Package | Role | Evidence and risk |
|---|---|---|
| `earthengine-api` (1.7.50 when read) | Earth Engine client | [V-doc] requires Python ≥ 3.11 (the project pins ≥ 3.11,<3.12, so compatible). Not installed; not downloaded for this assessment. Pin the exact version. |
| `google-auth`, `google-api-python-client`, `google-auth-httplib2`, `httplib2`, `requests`, `google-cloud-storage` | Transitive | [V-doc]. Whether any transitive package ships a native wheel that Windows Smart App Control blocks is **[X]**; each must be test-loaded on the target machine (A-13). |
| `httpx` | Used by the existing download code | Move from dev group to runtime (finding in section 2). |
| Frontend | None | MapLibre raster sources and existing components suffice. |

Installation, if approved, uses the project's lockfile process (`uv`), a clean environment check, and the full test suites. No
dependency is added by GEE-0.

## 21. Implementation phases

Each phase ends with a report and waits for approval. Phases that change the database say so, and **no migration is made
without its own separate approval**.

**Cross-cutting gates** (a phase may not start, or may not exit, until its gates are met):

| Gate | Requirement | Applies to |
|---|---|---|
| **G-T Commercial terms** | A-02: written organisation approval of the Google Cloud commercial terms for distributing remote previews and retaining exported imagery (and any caching). | GEE-3 may not show previews beyond the developers in a test project, and GEE-4 may not retain or deliver exports, until met. |
| **G-B Budget control (persistent)** | All of section 16.3a built and tested under concurrency, including Migration L separately approved and applied. | **Multi-user, multi-worker or production budget-controlled operation (operating model B).** Not required for GEE-1 (manual, owner-run) and not automatically required for GEE-2 or GEE-3, which may run in the controlled evaluation model (operating model A, 16.3a) while Migration L is not approved and implemented. |
| **G-A Controlled evaluation conditions** | The operating model A conditions of section 16.3a: named developers and one test project, one API process and at most one worker (single-instance guard), in-process atomic reservation with fixed low ceilings, local usage log, Cloud-side quota and billing alert, kill switch. | GEE-2 and GEE-3 (and GEE-4 and GEE-5) whenever G-B is not met. |
| **G-U User authorisation** | Section 6.6 (authentication, project membership, per-action roles, trusted attribution, review). | Any use by non-developers; mandatory before production use. |
| **G-K Kill switch** | Section 16.4 built and drilled. | Every phase from GEE-1. |

**Roadmap rule.** A phase's gates are `G-K` plus either `G-A` (controlled evaluation) or `G-B` (persistent budget control), and
`G-T`, `G-U` as stated per row. GEE-1 is always a manual, owner-run evaluation with no migration. If Migration L has not been
approved and implemented, GEE-2 and GEE-3 must remain within the controlled evaluation model: no second user, no second process
or worker, no non-developer use, no production use. None of the commercial, security, AOI-transmission, caching, CRS or
data-preservation restrictions is relaxed by operating model A.

| Phase | Scope | Gate to start | Exit criteria |
|---|---|---|---|
| **GEE-1 Foundation and spike** | Approve dependency; move `httpx`; settings (`auth_mode`, project); `client.py` with ADC/WIF/key-file branches; status and connection-test endpoints; dataset registry and probe; redaction extensions; kill switch; Settings tab (read-only). **Spike** against a non-production commercial project: ADC/WIF credential accepted by `ee.Initialize`; map ID lifetime and tile host; tile CRS; `getDownloadURL` and bucket export behaviour (zip vs TIFF, band names, multi-tile); measured EECU. **No database migration and no schema change.** **Evaluation limits:** a manual, owner-run evaluation only, with the Cloud-side quota and billing alert in force; no tile proxy, search service or export job exposed to anyone else; nothing retained (A-02); no area transmitted except by the owner's deliberate action; no caching. | A-01, A-03, A-04, A-05, A-07, A-08, A-09, A-13 and D-01–D-04 answered; G-K | Documented, repeatable connection; spike findings recorded; all limits in section 3.2 resolved or re-scoped |
| **GEE-2 Scene search** | AOI via `geo.convert`; scene-search endpoint; imagery screen (search and results); footprints; AOI-cloud fraction; project `allow_online` gating; audit events; mock-provider tests. Every search is an explicit person action with a "data leaves the machine" notice, reserved against the budget and recorded. **No schema change.** Runs in the controlled evaluation model (operating model A: named developers, one test project, one process, in-process reservation, fixed ceilings, local usage log) unless Migration L has been separately approved and applied. | GEE-1 report accepted; G-K; G-A (or G-B if Migration L is applied); Migration L is **not** required | Searches reproduce the Code Editor's counts for sample areas; every refusal path tested |
| **GEE-3 Remote preview layers** | Recipes, masks, true/false colour and index previews, tile proxy with signed parameter, remote group on the map, legends and attribution; tile reservations against the budget (in-process in operating model A, the persistent ledger in operating model B); project-scoped authorisation on every tile (section 13.3); no tile caching of any kind. **No schema change.** Without Migration L it stays within the controlled evaluation model (one process, named developers, one test project). Previews are shown only to developers in a designated test project until G-T and G-U are met. | A-11, A-12, D-07; G-K; G-A (or G-B if Migration L is applied); **G-T before previews are shown to anyone else**; **G-U before non-developer use** | Tiles verified visually against Earth Engine for sample dates; budget refusals tested; no credential or Google URL ever in a browser response (automated check) |
| **GEE-4 Export and ingestion** | `ee_export` job; both export paths; verification; ingestion as `online_import`; provenance record and asset block; **Migration E** (section 19), applied only after separate approval and backup; Save-as-local-raster UI. Exports are reserved against the budget before the task starts. Retained only once G-T is met; before that they are deleted after verification. Migration E does not lift the operating-model limits. | A-10, A-15, D-06, D-08, Migration E approval; G-K; G-A (or G-B if Migration L is applied); **G-T before any export is retained or delivered** | Round trip: export → ingest → map → tools works on a scratch project; originals byte-identical; no leftovers on failure or cancel |
| **GEE-5 Temporal analysis** | Composites, valid-observation count, before/after view, same-grid dual export for the comparison tool, time-series chart and CSV | D-05, D-12; G-K; G-A (or G-B if Migration L is applied) | Comparison tool runs on two exported dates; numbers match an independent calculation |
| **GEE-6 Hardening and operations** | Rate limits, quota alarms, ledger reports and reconciliation with Cloud usage, load and concurrency tests on tile traffic and workers, kill-switch drill, runbook, retention jobs, independent security review (including section 6.6), documentation | Accepted GEE-5; G-U built; **Migration L separately approved and applied (G-B)**, because GEE-6 hardens for multi-user, multi-worker operation | Budget cannot be exceeded under concurrent load; kill switch drilled; cross-project tile access shown impossible by test; threat model closed |
| **GEE-7 Final integration and client acceptance** | End-to-end integration of all earlier phases in the client's target environment with the client's identity provider, project, bucket, budget and ceilings; reconciliation against the original master specification; full regression of the existing suites plus the Earth Engine suites; a live acceptance run against the client's commercial project; confirmation of the preservation checks (originals, data, CRS policy, no ESM/Everest-56 claims); handover of the runbook and the budget/kill-switch procedures; client sign-off | Accepted GEE-6; A-01 to A-17 answered in writing; G-T, G-B, G-U and G-K all met | Client acceptance signed; production-use checklist (section 6.6, 16.3a, 16.4, 26) passed; remaining limitations recorded. Nothing is released to production use before this phase is accepted. |

## 22. Testing strategy

| Level | What | How |
|---|---|---|
| Unit | Recipe validation; limits; dataset registry; mask and index definitions (band mapping, scale and offset); redaction of every secret shape; AOI conversion; budgets | pytest, offline |
| Contract | The real provider against a stand-in `ee` that checks exactly what the code requests (the prototype's approach), extended to maps, masks, tasks | pytest |
| Provider fakes | Extend the mock provider (synthetic, labelled) to maps and tasks so the whole flow runs with no network | pytest |
| API | All endpoints, error codes, 403/409 paths, OpenAPI contract tests (the project uses Schemathesis) | pytest on the scratch database |
| Transformation | Exact, approximate, ambiguous, ballpark-only and grid-missing AOIs: each refused or accepted with the right record; guard test unchanged | pytest, mirrors Phase 3 |
| Security | No secret in any response, log, exception or audit row (automated scan); proxy host allow-list; tile requests without a valid signature rejected; local-only project refused; kill switch; path traversal in export names; oversized download stopped at the cap | pytest, scanners |
| Jobs | Cancel, retry, stale-worker recovery, partial-file cleanup, duplicates (same SHA-256), transaction rollback leaves nothing | pytest on the scratch database |
| Frontend | Component tests for each state; the Remote badge; no pixel tools for remote layers; accessibility | Vitest |
| Browser | Playwright against the real API with the mock provider: search, build layer, tile requests only to this app, export, ingest, map | Playwright on scratch servers |
| Budget control, operating model A | The single-instance guard refuses a second API process or worker for Earth Engine; concurrent requests in the one process against a small ceiling never exceed it (in-process atomic reservation); the local usage log is replayed at start-up and Earth Engine refuses to run if it cannot be read or written; ceilings cannot be changed from a request or the browser | pytest, no database change |
| Budget control, operating model B | Many simultaneous requests and workers against a small ceiling never exceed it (atomic reservation); a reservation fails closed when the ledger is unavailable; expired reservations are released; retries do not double-reserve; cancelled or failed jobs release; actual usage is recorded and reconciled; a ceiling cannot be changed from a request or the browser; the kill switch stops traffic with the database down | pytest on the scratch database with real concurrency (threads/processes) |
| Authorisation | Tile token for project X refused on project Y's layer; token for layer A refused on layer B; expired, tampered, wrong-recipe token refused; a user removed from the project refused immediately; archived or `local_only` project refused; "no such layer" and "not yours" indistinguishable; every Earth Engine endpoint checks membership and role server-side | pytest, API contract tests |
| Terms and retention gates | With A-02 not marked satisfied, no export is retained or ingested into a working project, no tile is cached anywhere (server or browser), and no preview is served outside the designated test project | pytest, browser test |
| Live (opt-in, never in CI) | A short scripted run against a **non-production** commercial project, run by the account owner with explicit approval, recording EECU, latency, and differences from the assumptions | manual, documented |
| Regression | The full existing backend (1,163), frontend (454) and browser (29) suites stay green after each phase | CI |
| Data-integrity | After each phase: working database untouched except by approved migrations; originals unchanged; checksums | read-only script |

A network-blocked test (as the prototype has) proves nothing contacts Google unless both switches and the project opt-in are on.

## 23. Security risk register

| ID | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| R-01 | Service-account key or federation token leaks (repo, logs, backup, support bundle) | Medium / Severe | Prefer keyless (section 6); if a key is unavoidable, protected path, rotation, no reads into settings, `.gitignore` safety net exists, log/redaction tests |
| R-02 | Tile endpoint abused to spend the organisation's money (any local profile, or anything that can reach the API port) | Medium / High | Project-scoped capability token re-authorised on every request (section 13.3), per-project opt-in, atomic reservations and hard ceilings (16.3a), concurrency and rate caps, global kill switch, bind address stays loopback by default |
| R-03 | Proxy becomes an SSRF forwarder | Low / High | Only the Earth Engine-issued address; Google-domain allow-list; no redirects followed; no user-supplied URLs |
| R-04 | Signed URLs or map tokens in logs, error text, support files | Medium / High | Redaction extended and unit-tested; exceptions wrapped; tokens in memory only |
| R-05 | A sensitive project area is sent to Google | Medium / High | `local_only` default; explicit per-project opt-in with a plain notice; audit event on every outbound call; A-07 decision recorded |
| R-06 | Local profiles are not authentication, and Earth Engine's organisation-level identity does not authorise application users, so any caller can act as any profile and attribution can be wrong | High (by design) / High | Section 6.6 requirements (authentication, project membership, per-action roles, trusted attribution, review) mandatory before production use; controlled evaluation only until then; server limits regardless of profile |
| R-07 | A downloaded or bucket GeoTIFF is malformed or hostile | Low / Medium | Size cap before reading; check on georeferencing, grid and bands; ingestion validation; never executed; downloaded files kept in their own scratch folder |
| R-08 | Dependency supply chain or a native wheel blocked by Smart App Control | Medium / Medium | Pin exact versions; test-load every wheel on the target machine; review the transitive list in section 20 |
| R-09 | Licence or Terms breach (noncommercial registration used, or distributing previews, caching, or retaining exports beyond the contract) | Medium / High | Commercial project only; A-02 is a hard gate (G-T) for distributing previews and retaining exports; no tile caching of any kind until confirmed in writing; attribution on every output |
| R-10 | Quota exhaustion or restricted throughput harms other users | Medium / Medium | Concurrency below 40, per-layer limits, 429 handling, cloud-side quota, monitoring |
| R-11 | Archive reprocessing changes pixels after the fact | Medium / Medium | Recipes pin scene IDs and registry versions; exports are the reproducible artefact and are stored with SHA-256 |
| R-12 | Wrong CRS or silent datum shift for the area or export | Low / High | All conversions through `geo.convert`; explicit CRS allow-list; grid checks on the delivered file; guard test |
| R-13 | A remote preview is mistaken for stored evidence | Medium / High | Distinct type, badge, legend text, no analysis tools on remote layers, reports label them; Save-as-local is the only path to evidence |
| R-14 | Cost overrun from a single large batch export | Medium / Medium | Pre-flight size estimate, area/bands/bytes ceilings, plan-level concurrency, monthly ledger refusal, billing alerts |
| R-15 | Dataset deprecation or permission change breaks a layer | Medium / Low | Probe results stored; clear `EE_DATASET_UNAVAILABLE`; recipe records the dataset ID and version |
| R-16 | Bucket misconfiguration exposes exports | Low / High | Private bucket, public-access prevention, uniform access, lifecycle delete, access limited to one identity, periodic check |
| R-17 | Account termination or inactivity (Terms §7.3 states 180 days for the consumer Terms) | Low / Medium | Local assets remain; remote layers degrade with a clear message; confirm commercial terms (A-02) |
| R-18 | Time-of-check/time-of-use differences between the search and the export (scenes added or removed) | Low / Low | Export pins the scene ID; the record states the retrieval time |
| R-19 | Budget overrun through a race: several requests, API processes or workers pass a check-then-act budget test at once, or a crashed process leaves budget unaccounted | Medium / High | Atomic database reservations, fail closed, expiring reservations with a sweeper, database-enforced task caps, concurrency tests, daily reconciliation with Cloud usage (16.3a) |
| R-20 | Cross-project access through a tile token or guessed layer ID | Medium / Severe | Token bound to one layer and one project; live re-authorisation of membership, project state and recipe hash on every tile; unguessable IDs with indistinguishable refusals; tests for every cross-project and cross-layer case (13.3) |
| R-21 | Previews are distributed or exports retained before the commercial terms permit it | Medium / High | G-T gate in section 21; evaluation-only limits; exports deleted after verification until A-02 is satisfied (14.4) |

## 24. Outstanding client decisions

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| D-01 | Where does the backend run? | Local/on-premises Windows host as today; a Google Cloud VM or Cloud Run | If feasible, Cloud Run/VM with an attached service account (no key). Otherwise WIF from an organisational IdP. |
| D-02 | What does "Organisation authentication" mean? | One organisation-owned service identity (assumed here); each staff member's own Google account | The service identity, with the app's own audit for attribution. |
| D-03 | If off Google Cloud and no IdP: accept a key file? | Yes under an exception and section 6.3; no | Only as a fallback, time-limited. |
| D-04 | Commercial plan and budget | Plan; monthly ceiling; alert recipients; daily EECU quota | Start with a low ceiling; raise it after measured use. |
| D-05 | Dataset scope for release 1 | S2 + Landsat 8/9 + S1 (this document) / add Landsat 5/7, DEM, WorldCover, MODIS | S2, Landsat 8/9 and S1 only. |
| D-06 | Maximum export area and path | ≤ 100 km² via direct download; larger via bucket batch (multi-file results need mosaicking or refusal) | Direct download in GEE-4; bucket in a follow-up once multi-tile handling is agreed. |
| D-07 | Tile caching | None / short TTL / disk cache | **None of any kind** (server or browser, `no-store`) until A-02 is approved in writing. |
| D-08 | Retention of exports, recipes, bucket contents | Duration; who may delete | Keep exports as normal assets; bucket 1 day; recipes with the project. |
| D-09 | Which projects may use Earth Engine | Any with opt-in / only named projects / only after roles exist | Opt-in per project plus an administrator-only enable. |
| D-10 | Who may create layers and exports | Roles over authenticated users (section 6.6) / controlled evaluation by named developers only | Authenticated users with per-action roles before any non-developer use; developers only until then. "Any profile" is not acceptable beyond evaluation. |
| D-11 | Cloud mask default | Cloud Score+ ≥ 0.60 / other threshold / off | Cloud Score+ ≥ 0.60, visible and removable. |
| D-12 | Analysis scope after previews | Indices only / thresholded masks / change maps | Indices and change screening labelled as such. No classification claims. |
| D-13 | Attribution and licence wording | As in section 16.5 / client wording | Client legal review. |
| D-14 | Data governance | Which areas must never leave the machine; approval workflow | Per-project explicit approval recorded in the audit history. |
| D-15 | Does the feasibility document stay? | Keep as history / mark superseded | Mark superseded for the noncommercial assumptions once this is accepted (not changed in GEE-0). |
| D-16 | How are application users authenticated and authorised? | Organisation single sign-on / application login; roles and project membership model; who administers them | Organisation single sign-on with project membership and the roles in section 6.6; part of, or equivalent to, the unapproved G6 work. |
| D-17 | Commercial terms approval (hard gate G-T) | Who reads and approves the Google Cloud commercial terms; what is approved: distributing previews, delivering and retaining exports, caching | Written approval per item; nothing is distributed or retained without it. |
| D-18 | Spending ceilings | Values per request, user, project, day and month; global ceiling; alert thresholds and recipients; who may change them | Set low for evaluation; raised only after measured usage; the application's ceilings sit below the Cloud-side ones. |

## 25. Preservation statement for GEE-0

- No application source file was edited; this file is the only addition.
- No schema or data was changed; no migration was written or run; the working database was not opened for this task.
- No dependency was downloaded or installed (only public documentation pages and the package index entry were *read*).
- No cloud resource was created, no Google Cloud setting changed, no credential requested, read or stored.
- No service was restarted. Nothing was committed, reset, cleaned or stashed. Uploaded originals are untouched.
- Earth Engine remains disabled and unconnected, as before.
- Revision 1 edited this one file only. It changed no source code, dependency, database, configuration, credential or cloud
  resource, and started no part of GEE-1.

## 26. Standing restrictions (preserved; apply to every phase)

| # | Restriction | Where it is enforced in this design |
|---|---|---|
| 1 | **No database migration without separate approval.** Each migration (L, E, or any other) is requested, backed up and tested on scratch databases first; none is applied by approving a phase. | Section 16.3a consequence, 19, 21 |
| 2 | **No credentials in browser code.** No token, key, map ID, signed URL, Cloud project identifier or upstream tile address reaches the browser; the only Earth Engine-related value it holds is a short-lived, project-scoped tile capability for this application's own API. | 5.1, 6.4, 13.2, 13.3 |
| 3 | **No automatic outbound AOI transmission.** Only an explicit person action, after a notice, sends an area (or anything derived from it); never page load, project creation or opening, a schedule or a background job. | 5.1 (principle 4), 8, 18 |
| 4 | **No persistent tile caching until the commercial terms are confirmed in writing**, on the server or in the browser. | 13.4, A-02, D-07 |
| 5 | **No CRS transformation outside the shared converter.** Ambiguous, ballpark-only and grid-missing cases are refused, never guessed; the guard test stays unchanged. | 15, 22 |
| 6 | **No ESM, Everest-56 or Polyconic assumptions.** Nothing here defines, supports or claims compatibility with them; Earth Engine data is in WGS 84-based CRSs and overlays on such sheets stay with the existing alignment tools. | 15 |
| 7 | Remote imagery stays distinguishable from local assets; private imagery and tiles are never publicly accessible. | 5.3, 13.3 |
| 8 | No dependency is added, no cloud resource created, and no Google Cloud setting changed without separate approval. | 20, 21 |
| 9 | Do not assume Earth Engine authentication gives access to every dataset; every dataset is probed. | 6.5, 7.3 |
| 10 | Uploaded originals are never modified; no commit, reset, clean or stash of the user's work is made by this work. | 14.3, 25 |

## Appendix A. Sources read on 2026-10-31

- Earth Engine access: https://developers.google.com/earth-engine/guides/access
- Authentication and initialisation: https://developers.google.com/earth-engine/guides/auth
- Service accounts: https://developers.google.com/earth-engine/guides/service_account
- Access control and IAM roles: https://developers.google.com/earth-engine/guides/access_control
- Quotas and limits: https://developers.google.com/earth-engine/guides/usage
- Image visualisation: https://developers.google.com/earth-engine/guides/image_visualization
- Exporting (overview and images): https://developers.google.com/earth-engine/guides/exporting and
  https://developers.google.com/earth-engine/guides/exporting_images
- Python installation: https://developers.google.com/earth-engine/guides/python_install
- Terms of Service: https://earthengine.google.com/terms/
- Datasets: https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_CLOUD_SCORE_PLUS_V1_S2_HARMONIZED,
  https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_CLOUD_PROBABILITY,
  https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD,
  https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC08_C02_T1_L2,
  https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC09_C02_T1_L2
- Application Default Credentials: https://docs.cloud.google.com/docs/authentication/application-default-credentials
- Secure-by-default organisations: https://cloud.google.com/resource-manager/docs/secure-by-default-organizations
- Package index entry: https://pypi.org/pypi/earthengine-api/json
- Pricing page (no usable figures returned): https://cloud.google.com/earth-engine/pricing
- Earlier research kept in the repository: `docs/earth-engine-feasibility.md` (2026-10-29), including the Sentinel-2 SR Harmonized
  catalogue page and `getDownloadURL` limits.

Pages that could not be read in full (content truncated by the fetch): `ee.Image.getMapId`, REST `computePixels`. Statements about
them are tagged [X].

## Appendix B. Revision 1 change log (documentation only)

| # | Review item | Where changed |
|---|---|---|
| 1 | Restore GEE-7 as a separate final integration and client acceptance phase | Section 21: GEE-7 row added; "final acceptance run" removed from GEE-6; GEE-6 retitled scope and exit; new cross-cutting gates table |
| 2 | Organisation authentication is not application-user authorisation; document the additional requirements | New section 6.6; summary finding 5; A-12 revised; A-16 added; R-06 revised; D-10 revised; D-16 added; gate G-U |
| 3 | Strengthen budget controls (atomic reservations, concurrent worker protection, recorded actual usage, hard ceilings, global kill switch) | New section 16.3a; section 16.4 rewritten as the global kill switch; section 16.3 table row replaced; section 19 (Migration L: ledger, reservations, events); summary finding 7 revised; A-17 added; R-19 added; D-18 added; GEE-2/GEE-3 rows and gates G-B, G-K; section 22 budget-control tests |
| 4 | Project-scoped authorisation mandatory on every remote tile request; a tile token must not give cross-project access | Section 13.3 rewritten; section 13.1 step 3; section 17 tile row; R-02 revised; R-20 added; section 22 authorisation tests |
| 5 | Commercial-terms approval is an explicit prerequisite for distributing previews and retaining exports | A-02 revised as a hard gate; summary finding 8 added; section 14.4 retention paragraph; section 21 gate G-T and GEE-3/GEE-4 rows; R-09 revised; R-21 added; D-17 added; section 22 terms-and-retention tests |
| 6 | Preserve the existing restrictions | New section 5.4 (pointer) and section 26 (full list); principle 4 in 5.1 strengthened (no automatic AOI transmission); 13.4 and D-07 strengthened (no caching of any kind, `no-store`); section 19 states no migration without separate approval |
| — | Housekeeping | Revision note near the top; "Seven findings" changed to "Eight"; section 25 notes that this revision edited this file only |

### Revision 2: roadmap clarification (documentation only)

| # | Instruction | Where changed |
|---|---|---|
| 1 | Keep Migration L as a proposed, separately approved migration | Section 19 (Migration L bullet: "a proposal that needs separate approval"); section 16.3a "Operating models" intro; summary finding 7; A-17. It remains unwritten and unapplied. |
| 2 | Required before multi-user, multi-worker or production budget-controlled operation, not automatically before GEE-2 | Summary finding 7; A-17 ("Needed by" column); section 19; section 16.3a (new "Operating models" subsection, replacing the former "Consequence for phasing" paragraph); section 21 gate G-B redefined (applies to operating model B only); GEE-2 and GEE-3 rows no longer list Migration L or G-B as gates and state "No schema change"; GEE-6 now requires Migration L applied (it hardens for multi-worker operation) |
| 3 | GEE-2 and GEE-3 stay within a controlled evaluation model if Migration L is not approved and implemented | New operating-model table and five rules in 16.3a (single API process, at most one worker with a single-instance guard, named developers, one test project, in-process atomic reservation with fixed low ceilings, local usage log, Cloud-side backstops, kill switch); new gate G-A and a "Roadmap rule" paragraph in section 21; GEE-2/GEE-3 gate cells; GEE-4 and GEE-5 note the same limits; section 22 gains an operating-model-A budget test row |
| 4 | Preserve GEE-1 as a manual, owner-run evaluation with no migration | GEE-1 row (now "No database migration and no schema change"; no caching added to its limits); 16.3a rule 1; section 19; Roadmap rule |
| 5 | Do not weaken commercial, security, AOI-transmission, caching, CRS or data-preservation restrictions | 16.3a rule 4 states operating model A weakens none of them and cites A-02/G-T, 13.3, 5.1, 13.4, 15, 26; the Roadmap rule repeats it; G-T, G-U, G-K, section 13.3 and section 26 are unchanged. Operating model A keeps G-K and the kill switch, fixed hard ceilings and fail-closed behaviour. |
| — | Housekeeping | Revision 2 paragraph near the top; "Seven findings" wording unchanged; no section renumbered |

No other file was changed.

## Appendix C. Code locations referred to

`services/api/geopoc/satellite/` (models, gate, provider, mock, earthengine, export), `config.py`, `jobs/queue.py`,
`jobs/handlers.py`, `ingestion/service.py`, `api/routers/layers.py` (layers, tiles), `api/schemas.py` (`RasterLayerOut`,
`LayersOut`), `db/models.py` (`Asset`, `Job`, `AssetLineage`, `Project`), `migrations/versions/0001` (source preference),
`0006` (origin, job kind, lineage checks), `geo/convert.py`, `geo/transform.py`, `geo/aoi.py`, `tests/test_transform_consistency.py`,
`apps/web/src/components/MapCanvas.tsx`, `apps/web/src/App.tsx` (routes), `docs/crs-conversion-coverage.md`,
`docs/earth-engine-feasibility.md`, `docs/architecture/decision-log.md` (U-013, U-014).
