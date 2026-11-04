# CDSE S3 authentication check

This integration answers one question: **do the S3 credentials you configured work against the Copernicus Data Space
Ecosystem (CDSE) object store?** It is the first step towards later phases (reading scene files). It downloads nothing,
writes nothing, and is not used by catalogue search, ingestion or any other feature yet.

Code: `services/api/geopoc/imagery/cdse_s3.py` (settings, status, the one request), `s3sign.py` (request signing),
`api/routers/imagery_s3.py` (the two routes). Tests: `services/api/tests/test_cdse_s3.py` (84 tests, all offline).

## What leaves the machine

Only when you ask for a verification, and only one request:

```
GET https://eodata.dataspace.copernicus.eu/eodata?delimiter=%2F&list-type=2&max-keys=1&prefix=Sentinel-2%2F
```

It is signed with AWS Signature V4. It carries your **access key id** (part of every S3 request) and a signature derived
from the secret key; the secret key itself is never sent. It lists at most one entry under a fixed public prefix. It
contains no project, no area of interest, no coordinates and no file names from your data. Nothing is downloaded, written
or deleted. The exact request is also returned in the `request` field of the verify response, and quoted in the
confirmation message.

## 1. Generate credentials

1. Sign in to your CDSE account (register at https://dataspace.copernicus.eu if you have none).
2. Open https://eodata-s3keysmanager.dataspace.copernicus.eu/ .
3. Choose **Add credentials**, set an expiry date, and confirm.
4. Copy the access key and the secret key. **The secret key is shown once** and cannot be displayed again.

Read the CDSE terms before use. Whether your use (including any commercial use) is permitted is for you to decide; nothing
in this application checks it.

> Never reuse keys that were ever pasted into a chat, a ticket, a commit or a document. Treat them as compromised:
> delete them in the key manager and generate new ones.

## 2. Configure locally

Put the values in the git-ignored `.env` at the repository root (never in `.env.example`, never in frontend code):

```env
GEOPOC_ONLINE_ENABLED=true        # the application-wide gate for anything that contacts a third party
CDSE_S3_ENABLED=true
CDSE_S3_ENDPOINT=https://eodata.dataspace.copernicus.eu
CDSE_S3_REGION=default
CDSE_S3_ACCESS_KEY=<your access key>
CDSE_S3_SECRET_KEY=<your secret key>
# CDSE_S3_TIMEOUT_S=10
```

Real environment variables take precedence over `.env`. Restart the API after changing them. Settings are read once per
process.

| Variable | Default | Meaning |
|---|---|---|
| `CDSE_S3_ENABLED` | `false` | Master switch. Off means nothing is ever sent. |
| `CDSE_S3_ENDPOINT` | `https://eodata.dataspace.copernicus.eu` | `https://host[:port]`, no path, query or credentials. Plain `http` is accepted only for localhost stand-ins. The alternative published endpoint is `https://eodata.ams.dataspace.copernicus.eu`. |
| `CDSE_S3_REGION` | `default` | Signing region. CDSE documents `default`. |
| `CDSE_S3_ACCESS_KEY` / `CDSE_S3_SECRET_KEY` | empty | Empty is treated as not set. |
| `CDSE_S3_TIMEOUT_S` | `10` | Per-request timeout, 1 to 60 s. |

## 3. Check status and verify

`GET /api/v1/imagery/s3/status` reads configuration only. It never contacts the store and never reports success:

```json
{
  "enabled": true, "configured": true, "authenticated": false,
  "status": "NOT_VERIFIED", "endpoint": "https://eodata.dataspace.copernicus.eu", "region": "default",
  "reason": "not_verified", "message": "Configured. Authentication has not been checked; run the verification to check it.",
  "online_gate_open": true, "checked_at": null
}
```

`POST /api/v1/imagery/s3/verify` with `{"confirm_external_request": true}` makes the single request above. Without the
confirmation the answer is `409 CDSE_S3_CONFIRMATION_REQUIRED` and nothing is sent. The route needs a workspace profile
header like the other routes. Nothing runs automatically at start-up or when the status is read.

| `status` | Meaning |
|---|---|
| `DISABLED` | `CDSE_S3_ENABLED` is not true, or `GEOPOC_ONLINE_ENABLED` is not true. Nothing sent. |
| `MISSING_CREDENTIALS` | A key is empty or not in a valid format. Nothing sent. |
| `NOT_VERIFIED` | Configured; no check made (status route only). |
| `AUTHENTICATED` | The store answered the signed request with a bucket listing. |
| `AUTHENTICATION_FAILED` | The store rejected the key, signature or expiry. See `reason`. |
| `CONNECTION_FAILED` | Timeout, network error, clock skew, bad endpoint, redirect, rate limit or an answer that is not an S3 listing. |

`reason` is a short stable code (for example `invalid_access_key`, `signature_mismatch`, `credentials_expired`,
`timeout`, `clock_skew`) and `message` is a fixed sentence chosen by that code. A TCP or TLS connection, or a 200 answer
that is not a bucket listing (a captive portal, for instance), is never reported as authenticated.

## Product metadata access check (HEAD)

Authentication proves the keys are valid for a listing. It does not prove the account can reach an individual product.
`head_object` in `cdse_s3.py` checks that with one signed `HEAD` of one metadata object: the answer carries the object's
size, type and modification time and **no body**, and none is read. It is a library function, not an API route, and is
not used by search or ingestion.

- The key must be a metadata file at the root of a Sentinel-2 L2A product folder
  (`Sentinel-2/MSI/L2A/YYYY/MM/DD/S2x_MSIL2A_….SAFE/MTD_MSIL2A.xml` or `manifest.safe`). Band files and anything else
  are refused before any request, so image content cannot be named.
- Results: `ACCESSIBLE` (200 with a length), `NOT_FOUND` (404), `ACCESS_DENIED` (401/403), and `CONNECTION_FAILED` with a
  reason (`moved` for a redirect, `rate_limited`, `server_error`, `timeout`, `unreachable`, `unexpected_response`). Only
  the HTTP code and validated header values (length, type, modification date, entity tag) are returned.
- Disabled, gated or credential-less configurations send nothing.
- Example product: the folder used in CDSE's own S3 documentation,
  `S2A_MSIL2A_20240115T235221_N0510_R130_T55HGS_20240116T021554.SAFE` (tile T55HGS, 15 January 2024). The root file name
  `MTD_MSIL2A.xml` comes from ESA's L2A product format; no CDSE page lists this product's files, so a 404 would mean the
  name or the product's availability differs, not that the credentials are wrong.

## Security precautions

- Credentials are read from the environment only, held as secret values, masked in `repr`, logs, settings dumps and
  OpenAPI, and never placed in a response or an error. Text written by the store is never repeated: only fixed messages
  are returned.
- Redirects are not followed, the answer is read up to 64 KiB, and the endpoint must be https.
- The client can make exactly one kind of request (a signed GET listing of at most one key). It has no method to read or
  write an object, and a test fails if the code gains a write verb, a download call, a database import or an import of
  the satellite, Earth Engine or CRS packages.
- The test suite pins `CDSE_S3_ENABLED=false` and placeholder keys in the environment, so tests (including the route
  fuzzing) can never use your real keys or reach the store.
- `.env` is git-ignored; `.env.example` holds only blank placeholders.

## Rotating credentials

1. Generate a new key pair in the key manager.
2. Replace both values in your local `.env` and restart the API.
3. Run the verification and confirm `AUTHENTICATED`.
4. Delete the old key pair in the key manager.

If a key may have leaked, do step 4 first.

## Current limitations

- **Not yet checked against the live service.** The request shape follows CDSE's published documentation (endpoint,
  region `default`, bucket `eodata`, listing under `Sentinel-2/`), but no real request has been made. CDSE does not
  document the signature version, the addressing style or which listing calls are permitted. This implementation assumes
  Signature V4 with path-style addressing (`/eodata?...`), which the published s3cmd configuration suggests. If the first
  real check fails with `authorization_malformed` or `unexpected_response`, those assumptions are the first thing to review.
- Verification proves the keys are accepted for a listing. It does not prove access to any particular product; the
  metadata HEAD check above is the next step, and reading content remains out of scope.
- Temporary credentials (the session-token flow) are not supported.
- Nothing yet reads scene files, ingests them or uses this status elsewhere; there is no UI.
- Status is not cached, and the last result is not stored (no database writes).
- Settings are read once per process; restart after changing `.env`.
