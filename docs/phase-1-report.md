# Phase 1 Report — Geospatial PoC

Date: 2026-10-12. Phase 1 is complete at its gate. Phase 2 has not been started.

**Exit criterion (specification V2 §13): create and reopen a project — met**, verified by an automated browser test and by API tests.

## 1. What was built

| Area | Delivered |
|------|-----------|
| Repository | Git initialised (branch `main`, nothing committed yet), `.gitignore`, `.gitattributes`, `.env.example`, README, dev and check scripts |
| Database | PostgreSQL 17.11 + PostGIS 3.5.7 in Docker (`postgis/postgis:17-3.5-alpine`), published on `127.0.0.1:5433` only, named volume `geopoc-pgdata`, credentials in git-ignored `.env` |
| Schema | Alembic migration `0001`: `workspace_user`, `project` (PostGIS geometry + GiST index), `audit_event`, `crs_definition` |
| API | `GET /health`, `GET /health/models`, `GET/POST /api/v1/workspace/profiles`, `POST/GET /api/v1/projects`, `GET/PATCH /api/v1/projects/{id}`, `GET /api/v1/crs` |
| API conventions | RFC 9457 error bodies with stable codes, `X-Request-ID` on every response, cursor pagination, audit rows for every change |
| Web app | Login / Local Workspace, Dashboard, New Project & Location, project overview, Settings (diagnostics, models, CRS, sources, storage); the other specified routes exist as placeholders that name the phase that delivers them |
| API client | TypeScript types generated from the OpenAPI document |

## 2. Checks run and exact results

All run on 2026-10-12 through `scripts\check.ps1`.

| Check | Result |
|-------|--------|
| Backend lint (`ruff check`) | Pass |
| Backend format (`ruff format --check`) | Pass, 33 files |
| Backend tests (`pytest`) | **45 passed**, 0 failed, 1 deprecation warning from a dependency |
| Frontend type-check (`tsc --noEmit`) | Pass |
| Frontend unit tests (`vitest`) | **13 passed** in 2 files |
| Frontend production build (`vite build`) | Pass |
| Browser end-to-end (Playwright on installed Edge) | **5 passed** |

Not run: no backend static type-checker (mypy/pyright) is configured; accessibility checks belong to Phase 6.

### Planned Phase 1 tests

| Test | Result | Evidence |
|------|--------|----------|
| Checklist item 1, first half: create a project without location | Pass | `test_create_project_without_location`; browser test 2 |
| Checklist item 14, partial: no secrets in settings displays or logs | Pass for the Phase 1 surface | `test_secrets.py`: the real database password is absent from API responses, the OpenAPI document, captured logs and the settings representation; `.env` is git-ignored |
| PT-01 API contract tests from OpenAPI | Pass | `test_contract.py`: Schemathesis, 6 operations, up to 40 generated cases each, checking server errors, status codes, content types and response schemas |
| PT-02 migrations apply and roll back | Pass | `test_migrations.py`: upgrade → downgrade → upgrade on an empty scratch database |
| PT-03 incomplete CRS rejected | Pass | "WGS84 + LCC", "WGS84", "Lambert Conformal Conic" and an unknown code all return `CRS_UNRESOLVED`; nothing is stored |
| PT-04 browser: profile → empty dashboard → create → reopen | Pass | Browser tests 1 and 2 |
| PT-05 health reports an unavailable database | Pass, with a caveat | The connection failure was simulated by pointing at an unused port. The container was not actually stopped. |

The contract test found one real defect during the phase: a malformed JSON body returned an undocumented 400. It is now documented and tested.

Acceptance tests T-01 to T-12 are not applicable to Phase 1 and were not run. No claim is made about them.

## 3. Safeguards verified

| Safeguard | How it is enforced | Verified by |
|-----------|--------------------|-------------|
| Loopback only | Database port published on 127.0.0.1; Vite bound to 127.0.0.1; the API entry point refuses any other host | `test_refuses_non_loopback_bind`; `docker ps` shows `127.0.0.1:5433->5432` |
| Profile is not authentication | Stated on the login screen, in the API response and in the README | Browser test 1 |
| No data leaves the machine | No external URLs in the app; PROJ network off; online sources off | Browser test 5 records every request and asserts none is non-loopback; `test_proj_network_access_is_disabled` |
| No cloud AI | No model or LLM is contacted in Phase 1; the manifest loader refuses non-local entries | `test_manifest_refuses_remote_models` |
| No guessed CRS | Only authority codes resolvable in the local registry are accepted | PT-03; browser test 3 |
| AOI is context, not evidence | Stored as entered with its CRS; EPSG:4326 copy linked to a recorded transformation; never repaired | AOI tests in `test_projects.py` |
| Scope | No targeting, weapon, engagement or impact-point concept exists anywhere in the schema, API or UI | By construction |
| Specification preserved | SHA-256 of the DOCX unchanged: `61A16836…812D58` | Hash compared after the move |

Not verified in this phase, as instructed: CUDA-enabled PyTorch, any vision model, local Qwen inference. None was installed, downloaded or run.

## 4. Deviations from the plan and things you should know

1. **Project location.** The approval named `D:\geopoc`; you then asked mid-phase for everything to be in the workspace folder, so the project is at `C:\Users\HP\Desktop\target`. Package caches (`D:\geopoc-cache`) and the data folder (`D:\geopoc-data`) stay on D:. C: has 7.0 GB free; the project uses about 0.5 GB there.
2. **Leftover folder.** `D:\geopoc\docs` remains with an identical copy of the docs. My attempt to remove it was blocked by the tool's safety rule, so it is yours to delete.
3. **Smart App Control.** Windows Smart App Control is enforcing on this machine and blocked the native libraries in `psycopg-binary 3.3.6` and `pyproj 3.7.2`. I did not touch the policy. `psycopg[binary]==3.2.10` and `pyproj==3.7.1` load and are pinned. Expect the same check to be needed for Rasterio/GDAL in Phase 2 and CUDA PyTorch in Phase 3.
4. **Database files are on C:.** The named volume lives inside Docker Desktop's disk image on C: (4.3 GB before, plus a 729 MB image). Docker data was not moved. A bind mount to D: was avoided because PostgreSQL data directories on Windows bind mounts are unreliable.
5. **Synchronous database access.** The blueprint said async SQLAlchemy; the implementation uses synchronous sessions in FastAPI's thread pool, which is simpler and avoids a Windows event-loop limitation of the driver. Revisit only if needed.
6. **IDs are UUID version 4**, not version 7 as the blueprint suggested (Python 3.11 has no built-in v7).
7. **Endpoints added beyond the blueprint catalogue:** `GET/POST /api/v1/workspace/profiles`, needed for the approved named-profile decision. Mutating endpoints require an `X-Workspace-Profile` header.
8. **Not delivered from the Phase 1 plan:** `GET/PATCH /api/v1/settings` (the Settings screen reads `/health` instead); a backend static type-checker.
9. **TypeScript is pinned to 5.x** because the API type generator does not yet support TypeScript 7.
10. **Nothing is committed.** Git is initialised and `.env` is ignored; the first commit is left for you to request.
11. **Scratch databases** `geopoc_test` and `geopoc_e2e` exist in the container for tests.

## 5. Unresolved documentation discrepancy

The approved file `docs/Geospatial_PoC_UI_and_AI_Analysis_Rules_v3.docx` is the working specification. Its filename and its final section say Version 3.0; its cover page says "Revision 2.0" and its page footer says "Version 2.0". This is recorded as unresolved (decision log S-01). The document has not been modified.

## 6. Blockers and decisions before Phase 2

Phase 2 (ingestion, validation, metadata, previews, job worker, observation contract) needs:

| Needed | Reference |
|--------|-----------|
| Ruling on the two observation schemas and two review-status vocabularies in the specification | D-008, D-009 |
| Duplicate-upload policy (proposed: accept, link, flag) | D-013 |
| Supported file formats and maximum sizes per modality | U-003 |
| Approval to install Rasterio, OpenCV and related packages, subject to the Smart App Control check | This report §4.3 |
| A few sample files per modality, if real samples are to be used alongside synthetic fixtures | Readiness §4 |

## 7. How to run it

```powershell
.\scripts\dev.ps1      # then open http://127.0.0.1:5173
.\scripts\check.ps1    # all checks
```
