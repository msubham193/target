# Earth Engine, GEE-1: setup and manual evaluation

**Status (2026-10-31).** GEE-1 is implemented and tested **offline**. It has **not been verified against the live service**:
this host has no Earth Engine project settings, no Application Default Credentials and no credentials file, and none was
requested or created. Everything below that touches Google is for the **account owner** to run by hand. Read
[earth-engine-integration-assessment.md](earth-engine-integration-assessment.md) (sections 6, 16.3a, 16.4, 21 and 26) first.

## What GEE-1 is, and is not

GEE-1 is a **controlled evaluation** (operating model A): named developers, one designated Cloud project, one host, one API
process and at most one worker, fixed low usage ceilings kept by the server, an append-only usage log outside PostgreSQL, a
global kill switch, and fail-closed behaviour.

| GEE-1 does | GEE-1 does not |
|---|---|
| Builds credentials for an **organisation-owned** identity and initialises Earth Engine for the designated project | Send an area of interest or any geometry |
| Runs one trivial computation to prove the identity may compute | Draw or serve tiles, or show previews |
| Offers a read-only catalogue of six datasets and a manual availability probe that asks only for a collection's time extent | Export, download or keep any imagery |
| Records every outbound request in a local log and refuses past fixed ceilings | Use the database for anything but identifying the profile (no migration, no schema change, no write) |
| Closes the older prototype's search and export route | Run on a timer or in the background |

## Prerequisites (account owner)

| # | Needed | State on this host |
|---|---|---|
| 1 | A Google Cloud project dedicated to this application, **registered for commercial Earth Engine use**, with a billing account and an agreed plan, and the Earth Engine API enabled | Not present / not verified |
| 2 | An **organisation-owned identity** the backend can use, by one of: an attached service account (backend on Google Cloud), workload identity federation, or an impersonated service account. A key file is a last resort needing a written exception | None present |
| 3 | IAM for that identity on the project: `roles/earthengine.writer` (or a custom role holding `earthengine.computations.create`) **and** `roles/serviceusage.serviceUsageConsumer`. Viewer alone is not enough to compute | Not present |
| 4 | **Cloud-side backstops**: the per-day EECU-time quota and a concurrency limit for the project, and a Cloud Billing budget with alert thresholds | Not present |
| 5 | Written approval of the Google Cloud commercial terms (assessment A-02) before anything is shown to anyone but the developers or any export is retained | Not given; GEE-1 retains nothing |
| 6 | Outbound HTTPS from this host to `earthengine.googleapis.com`, `oauth2.googleapis.com`, `www.googleapis.com` (and the metadata server if on Google Cloud) | Not tested |
| 7 | The client package: `uv sync --inexact --group earthengine` | **Done** (earthengine-api 1.7.50 and its Google client libraries; every dependency loads on this machine under Windows Smart App Control; nothing else was removed or changed) |

`gcloud` is installed on this host. Running `gcloud auth ...` is an owner action and was not done.

## One-time setup (owner-run)

1. **Create the control folder** outside the repository, for example `D:\geopoc-earthengine`:

   ```powershell
   . .\scripts\env.ps1
   $env:GEOPOC_EARTH_ENGINE_CONTROL_DIR = 'D:\geopoc-earthengine'   # or put it in .env
   cd services\api
   uv run --no-sync python -m geopoc.earth_engine init
   ```

   It holds three things: `KILL` (the kill switch, present only while engaged), `instance.lock` (the single-instance lock)
   and `usage.log.jsonl` (the usage log). Back it up like any audit file; do not edit it.
2. **Settings** in `.env` (git-ignored; never put a key or token in it). All are commented in `.env.example`:

   | Setting | Meaning |
   |---|---|
   | `GEOPOC_ONLINE_ENABLED=true` and `GEOPOC_EARTH_ENGINE_ENABLED=true` | Both global switches; either off means off |
   | `GEOPOC_EARTH_ENGINE_PROJECT` | The one designated Cloud project id |
   | `GEOPOC_EARTH_ENGINE_AUTH_MODE` | `adc`, `wif`, `impersonation` or `key_file` |
   | `GEOPOC_EARTH_ENGINE_CONTROL_DIR` | The folder from step 1 |
   | `GEOPOC_EARTH_ENGINE_DEVELOPERS` | Comma-separated display names of the profiles allowed to call the API endpoints |
   | `GEOPOC_EARTH_ENGINE_MAX_REQUESTS_PER_DAY` (40), `..._PER_OPERATION` (3), `..._MAX_CONCURRENT` (1) | Ceilings: may be lowered, never raised past the bounds in `geopoc/earth_engine/usage.py` (100, 5, 1). A larger value makes Earth Engine refuse to run |
3. **Authenticate as the organisation-owned identity.** Pick the route that matches where the backend runs:

   | Mode | How (owner-run; shown for orientation) |
   |---|---|
   | `adc` on Google Cloud | Run the backend on a VM or Cloud Run with the service account attached; nothing to configure on the host |
   | `wif` | Create a workload identity pool and provider for the organisation's identity provider, generate the **credential configuration file** (it holds no key), and set `GOOGLE_APPLICATION_CREDENTIALS` to that file. The code requires the file's `type` to be `external_account` |
   | `impersonation` | `gcloud auth application-default login --impersonate-service-account=<SERVICE_ACCOUNT_EMAIL>`. This ties the service to one person's login on the host, so it suits a controlled evaluation only |
   | `key_file` | Last resort. Needs a written administrator exception (new organisations block key creation by default), `GEOPOC_EARTH_ENGINE_ALLOW_KEY_FILE=true`, `GEOPOC_EARTH_ENGINE_CREDENTIALS_FILE=<path outside the repository and the data folder>`. The application stores only the path |

   The code **refuses an individual person's own Google credentials**, and refuses ADC that resolves to a service-account key
   unless the mode is `key_file` with the exception switch. Which mechanism Earth Engine's client accepts for federated or
   impersonated credentials is the first thing the evaluation must establish (assessment, section 3.2).
4. **Set the Cloud-side backstops** (prerequisite 4) before the first call.

## Running the check

One process at a time may use Earth Engine on this host. The command line takes the same lock as the API.

```powershell
. .\scripts\env.ps1
cd services\api
uv run --no-sync python -m geopoc.earth_engine status      # local only; nothing is sent
uv run --no-sync python -m geopoc.earth_engine datasets    # the read-only catalogue
uv run --no-sync python -m geopoc.earth_engine verify      # the connection check (3 requests)
uv run --no-sync python -m geopoc.earth_engine probe landsat-8-c2-l2   # one dataset (1 request, 3 if cold)
```

Or, with the API running, as a profile listed in `GEOPOC_EARTH_ENGINE_DEVELOPERS` (do not run the command line at the same
time; stop one or the other):

| Endpoint | Purpose | Sends to Google |
|---|---|---|
| `GET /api/v1/earth-engine/status` | Switches, control folder, kill switch, today's use against the ceilings, last check | Nothing |
| `GET /api/v1/earth-engine/datasets` | The catalogue with each dataset's latest probe result | Nothing |
| `POST /api/v1/earth-engine/connection-test` | Initialise as the organisation-owned identity and run `1 + 1` on the service | Credentials exchange and one computation; no geometry |
| `POST /api/v1/earth-engine/datasets/{slug}/probe` | Ask for one collection's first and last image time | One query; no geometry |

A result of `ok: true` from `connection-test` is the first **live** evidence that the identity, project, roles and API are
right. Until the owner has run it, the status endpoint reports `live_verified: false`.

### Costs against the ceilings

Each unit is one outbound request. A connection check costs 3 (credential exchange and initialise: 2, computation: 1). A probe
costs 1, or 3 when Earth Engine has not yet been initialised in the process. The default daily allowance is 40 units, one
operation at a time. The allowance is per UTC day, survives restarts, and a request that would pass it is refused before
anything is sent.

### If a check fails

A failed check is reported (`ok: false`, a code, and a next step). Common ones:

| Message says | Fix |
|---|---|
| `individual person's Google account` | Use an attached, federated or impersonated service identity |
| `long-lived service-account key` | Do not resolve ADC to a key; see the key-file conditions |
| `serviceUsageConsumer` | Grant `roles/serviceusage.serviceUsageConsumer` on the project |
| `not registered for Earth Engine` / API not enabled | Register the project (commercial) and enable the Earth Engine API |
| `Permission denied ... earthengine.computations.create` | Grant `roles/earthengine.writer` or a custom role with that permission |
| `quota or rate limit` | Wait, or review the Cloud-side quota |
| `EE_BUDGET_EXCEEDED` | The server's own ceiling; it resets at 00:00 UTC. Do not edit the log to get around it |
| `EE_INSTANCE_CONFLICT` | Another process (the API or the command line) is using Earth Engine |
| `EE_KILL_SWITCH` | Engaged; see below |
| `EE_LOG_UNAVAILABLE` | The usage log cannot be read or written; see below |

## The kill switch

```powershell
uv run --no-sync python -m geopoc.earth_engine kill "reason"   # stops everything at the next request or step
uv run --no-sync python -m geopoc.earth_engine resume          # a deliberate, separate step; nothing resumes by itself
```

It is a file in the control folder, checked before every request and between the steps of a check, with no database and no
restart. If the folder is missing, unreadable or the flag cannot be read, Earth Engine is treated as stopped. Creating a file
named `KILL` by hand does the same. Run `kill` once as a drill before the first live check, and confirm `status` shows it.

## The usage log

`usage.log.jsonl` in the control folder is append-only JSON lines. Each line carries the hash of the one before it, so an edit
or a lost line is noticed. It records the operation, units, an actor label, the outcome and a short detail code. It never
holds a credential, an area, a coordinate, an image or a signed URL. It is an evaluation aid, not the audit record of record.

If Earth Engine refuses with `EE_LOG_UNAVAILABLE` and "damaged", run `python -m geopoc.earth_engine verify-log`, inspect the
file, and only then decide: archive it (rename it) and start a new one. That **resets the day's allowance**, so it is a
deliberate owner decision and should be noted in your records.

## Manual evaluation checklist (owner-run)

Record each result with the date and who ran it.

1. [ ] `kill`, `status` (engaged), `resume`, `status` (off): the drill.
2. [ ] `verify` with the intended mode: `ok: true`, the `credential_kind` and `service_account_email` are the organisation's.
3. [ ] `verify` with a person's own `gcloud` credentials on a scratch shell: refused (negative test).
4. [ ] Start `verify` in two shells at once: the second is refused with `EE_INSTANCE_CONFLICT`.
5. [ ] `probe` each of the six slugs. Compare each `extent_start` with the documented start date and with the Code Editor;
   note any dataset that is not `available` (assessment A-06).
6. [ ] In the Cloud console, note the EECU usage and billing before and after steps 2 and 5 to estimate the real cost per
   request (assessment section 16.3, "Measuring real cost").
7. [ ] Lower `GEOPOC_EARTH_ENGINE_MAX_REQUESTS_PER_DAY` to 3 and confirm the second `verify` is refused with the ceiling message
   and nothing is sent.
8. [ ] Confirm the Cloud-side quota and billing alert exist and who receives them.
9. [ ] Record what remains unknown for GEE-2 and later (map ID lifetime, tile host, tile CRS, export behaviour); those need
   code that GEE-1 does not contain and were not tested.

## Restrictions in force

No migration and no database write; no credential in the browser or any response; no area of interest sent; no tile, preview,
export or retained imagery; no persistent cache; no coordinate transformation (the shared converter is untouched); no
ESM, Everest-56 or Polyconic assumption. Anything beyond GEE-1 needs separate approval, and so does any migration (including
the proposed ledger, Migration L).
