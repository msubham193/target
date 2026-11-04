# Satellite catalogue discovery (Phase 1)

Find Sentinel-2 Level-2A scenes over a project's area and dates through a STAC catalogue. **Discovery only**: nothing is
downloaded, nothing is written to the database or to disk, and results are returned to the caller and kept nowhere.
The code is in `services/api/geopoc/imagery/` and `services/api/geopoc/api/routers/imagery.py`. It is independent of
`geopoc.satellite` and `geopoc.earth_engine`, which are unchanged.

## What leaves this machine

A real search sends one HTTPS `POST {catalogue}/search` containing: the collection, a **bounding box rounded outward to
`GEOPOC_IMAGERY_AOI_ROUND_DEG` degrees (default 0.01, about 1 km)**, the date window, the cloud limit, the page size, a
sort order and, on later pages, the catalogue's own paging cursor. It does **not** send the exact polygon, the project
or profile, or any file. The exact polygon is used locally to drop scenes whose footprint lies outside it. If a
bearer token is configured it is sent as an `Authorization` header to the configured address only.

## Gates (all required, checked in this order)

1. The request is valid (area, dates, cloud limit, page size, provider, collection, page token) - always checked, and
   checked first, so a bad request never reaches the gates.
2. `GEOPOC_ONLINE_ENABLED`, `GEOPOC_IMAGERY_ENABLED` and `GEOPOC_IMAGERY_TERMS_CONFIRMED` are all true. The last one
   records that the account owner read the provider's terms; commercial use is **not** assumed to be allowed.
3. The project's source preference is `allow_online` and it is not archived.
4. The request itself carries `confirm_external_transmission: true`. Without it the answer is a 409 that states exactly
   what would be sent. `dry_run: true` shows the same disclosure as a normal 200 and sends nothing, whatever the gates.

## API

`GET /api/v1/imagery/providers` - providers, collections, which gates are closed. Local only; shows no credential.

`POST /api/v1/projects/{project_id}/scene-search`

```json
{
  "provider": "cdse",
  "collection": "sentinel-2-l2a",
  "aoi": {"type": "Polygon", "coordinates": [[[10.01, 50.01], [10.05, 50.01], [10.05, 50.05], [10.01, 50.05], [10.01, 50.01]]]},
  "start_date": "2024-05-20",
  "end_date": "2024-06-20",
  "max_cloud_percent": 50,
  "limit": 20,
  "dry_run": false,
  "confirm_external_transmission": true
}
```

The area is a GeoJSON Polygon in EPSG:4326 (longitude, latitude); no coordinate conversion is done. Page by repeating the
identical request with `"page_token"` set to the previous answer's `next_page_token` (a token from a different search is
refused). Cloud cover is the catalogue's figure for the whole granule, not for the area. A scene with no acquisition time
is skipped and counted under `filtering.malformed`; an unknown cloud value is never guessed (with a cloud limit such a
scene is dropped and counted under `filtering.unknown_cloud`).

Refusals are `application/problem+json` with a stable `code`: 409 `IMAGERY_DISABLED`, `IMAGERY_PROJECT_LOCAL_ONLY`,
`IMAGERY_PROJECT_ARCHIVED`, `IMAGERY_CONFIRMATION_REQUIRED`, `IMAGERY_NOT_CONFIGURED`; 422 for invalid input (`AOI_*`,
`DATES_*`, `CLOUD_RANGE`, `LIMIT_RANGE`, `IMAGERY_UNKNOWN_PROVIDER`, `IMAGERY_PAGE_TOKEN_INVALID`); and for a real
catalogue failure 429 `IMAGERY_UPSTREAM_RATE_LIMITED`, 502 `IMAGERY_UPSTREAM_*` / `IMAGERY_BAD_RESPONSE`, 504
`IMAGERY_UPSTREAM_TIMEOUT`.

## Not verified

The catalogue address, the collection name `sentinel-2-l2a`, the `query` filter on `eo:cloud_cover`, the `sortby`
extension and the property names read from items come from public documentation as remembered and were **not checked
against the live service** (no live request is made in Phase 1). The cloud filter is therefore applied again locally.
Whether search needs a token is also unverified. The first live request needs the owner's approval.

## Audit trail

A search writes one log line (`geopoc.imagery`) with the provider, project, profile, a short fingerprint of the box and
the number of scenes returned - never coordinates or credentials. A durable record in `audit_event` would be a database
write and is not part of Phase 1.
