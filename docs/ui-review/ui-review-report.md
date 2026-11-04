# UI Review Report — Phase 3.5

Date: 2026-10-12. Scope: the Phase 1–3 interface only. No Phase 4 work was started, and no future-phase screen was built to make the interface look complete.

## 1. How the review was done

- The application was started with the documented command, `scripts\dev.ps1`, against the working database, with the real worker on the RTX 5060 and the real model `fasterrcnn_resnet50_fpn_v2`.
- A walk-through script (`apps/web/review/capture.spec.ts`) drove the installed Microsoft Edge through every implemented screen, at 1366 × 800 and at 390 × 844 (phone width), and saved 49 screenshots to `docs/ui-review/screenshots/`. It also recorded every browser console error and failed request to `docs/ui-review/browser-log.json`.
- The screenshots were then **looked at one by one**. That inspection is what found the defects below; the script only produces the pictures.
- Inputs: three public-domain sample photographs, synthetic rasters (georeferenced, without a CRS, complex-valued) and a 3-second test video, all kept outside the repository in `D:\geopoc-cache\review`.
- Three states cannot be produced on demand — a queue full of jobs in every state, a missing model, a stopped worker — plus an unreachable API. For those, one API answer was replaced with fixed data inside the browser. Their screenshots are named `…-injected`. Everything else is the real application.
- The walk-through was run three times: once to find problems, then twice after fixing them.

This is a review by an assistant looking at screenshots. It is not your visual approval; section 7 lists how to repeat it yourself.

## 2. Screens reviewed

| Screen | Wide | Narrow | States seen |
|---|---|---|---|
| Login / local workspace | 01 | — | first use, service health |
| Dashboard | 02, 39, 40 | 42 | empty, with a project, no search match, API unreachable (41) |
| New project & location | 03–07 | 49 | blank, missing name, location without CRS, "WGS84 + LCC" not found, CRS results, filled |
| Project overview | 08 | 43 | with a location |
| Upload | 09–11 | 44 | empty; stored, duplicate, signature mismatch, corrupt, unsupported type, long file name; raster and video |
| Asset list | 12–14 | 45 | previews still generating, ready, filtered |
| Asset detail — photo | 15–17 | 46 | before analysis, job queued, job finished |
| Asset detail — others | 22–27 | — | duplicate, georeferenced raster, raster without CRS, unsupported preview, video, long name |
| Job panel states | 28–30 | — | queued, checking inputs, processing with progress, failed, cancelled, completed with warnings, completed with no findings; model unavailable; worker not running |
| Ground-image results | 18–21 | 47 | findings with boxes, row highlight, two runs, not yet analysed |
| Settings | 31–35 | 48 | diagnostics, models, CRS, sources, storage |
| Placeholders and errors | 36–38 | — | Map placeholder, project not found, page not found |

## 3. Issues found and fixed

| # | Issue | Where | Fix |
|---|---|---|---|
| 1 | The dashboard said "Processing jobs are not part of this phase" — false since Phase 3 | Dashboard | Now says where jobs are shown |
| 2 | The project overview said "Running analysis is not part of this phase. This project has no jobs or observations" — false | Project overview | Replaced with how to reach a file's jobs |
| 3 | Dates showed as `12/10/2026`, which reads as 12 October or 10 December depending on the reader | Everywhere | One format: `12 Oct 2026, 17:10` |
| 4 | Byte counts used the computer's regional grouping (`7,91,555`) | Asset detail | Fixed grouping: `791,555` |
| 5 | Two side-menu items highlighted at once on the upload page ("Upload" and "Assets"), and **none** highlighted on a file's page or its results | Project menu | Exactly one current section on every project page; tested |
| 6 | Thumbnails stayed on "generating" until the page was reloaded, although the worker had finished | Asset list | The list keeps checking while any preview is pending |
| 7 | The location was shown as raw JSON with 17-digit floats (`75.00000000000001`) and a raw PROJ pipeline string | Project overview | Plain text ("X 500000, Y 3000000 in EPSG:32643"; derived copy shown to 6 decimals, labelled as rounded for display); the transformation is behind "How the EPSG:4326 copy was derived" |
| 8 | A form error stayed on screen after the problem was corrected | New project | The error clears as soon as the form changes; tested |
| 9 | Retry was offered on a preview that can never succeed (complex-valued raster) | Job panel | Not offered; the panel says why; tested |
| 10 | "View results" on an older job opened the latest run, not that job's run | Job panel → results | Each job links to its own run; tested |
| 11 | "2 finding(s) stored", "0 finding(s) stored" | Job panel | "2 findings stored", "No findings stored", "1 finding stored"; tested |
| 12 | A raw code `UNCALIBRATED_SCORE` appeared next to readable labels | Results table | Readable label "Score not calibrated"; unknown codes are turned into words, never shown raw; tested |
| 13 | `GTiff` shown as a format name | Asset list, detail | "TIFF / GeoTIFF" |
| 14 | "What is not known" used field names (`acquisition time`, `crs`) | Asset detail | "Capture time", "Coordinate reference system", "Location" |
| 15 | Raster bounds shown as JSON; CRS status shown as `missing`; the geotransform label wrapped awkwardly | Asset detail | Plain text; "Missing — the file declares none"; shorter labels |
| 16 | Sharpness read "861.9 (raw value; Raw indicator only. …)" | Asset detail | "861.9 — a raw measurement only. No validated threshold exists, so it makes no blur judgement." |
| 17 | On phone width, the SHA-256, the results table and the provenance list ran past the right edge | Asset detail, results | Long values wrap; the table scrolls inside its own box |
| 18 | On phone width, the job panel's status badge and model name collided | Job panel | Wraps cleanly |
| 19 | The progress bar was drawn in the browser's default green and grey | Job panel | Uses the interface's blue |
| 20 | "Switch" next to the profile name was ambiguous | Header | "Switch profile" |
| 21 | Planned sections (Map, Observations, Reports, 3D) looked identical to working ones | Project menu | Marked "planned" and greyed |
| 22 | No breadcrumb from a file or its results back to the asset list | Asset detail, results | Breadcrumb added |
| 23 | When the run selector appeared, nothing said which run was newest | Results | "(latest)" on the newest |
| 24 | The browser requested a missing `/favicon.ico` on every page (two 404s per load) | All | Inline icon; no request |
| 25 | **Backend:** an upload failed with `STORAGE_ERROR` if the temporary-upload folder had been deleted while the API ran (found when resetting data between review runs) | Upload | The folder is recreated when needed |
| 26 | Settings → Models described the model as "Pipeline: ground_image · task: object_detection" | Settings | "For: ground photographs · task: object detection · runs on this computer" |
| 27 | Settings → Models licence line did not reflect the approved-use decision | Settings | Now says "approved for local PoC evaluation only; licensing review of the weights is pending" (from the manifest) |

No backend contract was changed. Fix 25 is an internal storage fix; fix 27 is a text change in `models/manifest.yaml`.

### Browser console and network

After the fixes, the final walk-through recorded 5 console errors and 5 failed requests. **All are expected:**

| Request | Why |
|---|---|
| `415 POST …/assets` | The deliberately mismatched upload (`renamed_png.jpg`) |
| `422 POST …/assets` | The deliberately truncated upload |
| `404 GET …/projects/<id>` | The deliberate "project not found" check |
| Two aborted requests | The deliberately simulated API outage |

No JavaScript error occurred. No request went anywhere other than `127.0.0.1`.

## 4. Remaining defects

| # | Defect | Impact | Why not fixed now |
|---|---|---|---|
| R1 | On phone width the asset list is a table that scrolls sideways; rows become tall because the hidden columns still set row height | Usable but awkward on a phone | Needs a card layout for narrow screens; a layout redesign rather than a fix. This is a desktop workbench first. |
| R2 | The second "person" box on the sample photograph (score 0.68, a strip at the image edge) is probably a false positive | Shows the model's limits; no UI defect | Correct behaviour: it is marked "needs review". There is no way to reject it until Phase 4. |
| R3 | Long model identifiers (`fasterrcnn_resnet50_fpn_v2 coco_v1`) are technical | Readable but not friendly | The manifest has no display name field; adding one is a small contract change best done with the model registry work |
| R4 | Raster previews of 1-band data are greyscale and stretched; nothing in the image says so beyond the caption | Could be read as the true values | The caption says brightness is adjusted; a legend belongs with the raster viewer (Phase 4) |
| R5 | The dashboard has no cross-project job list | Jobs are only visible per file | Not in scope; the dashboard says so |
| R6 | Archived projects can still be opened and their files viewed, but uploads are refused with only a banner on the upload page | Minor | Behaviour is correct; could be clearer on other pages |
| R7 | Colour contrast was judged by eye only; no automated accessibility checker was run | Unknown | Accessibility checks are Phase 6 in the plan |
| R8 | Keyboard-only use was not walked through | Unknown | Same |
| R9 | The asset list's "none" for a file without a preview does not say why | The reason is on the file's page | Minor |

## 5. Tests actually run

Final run of `scripts\check.ps1`, 2026-10-12:

| Check | Result |
|---|---|
| Backend lint and format | Pass |
| Backend tests | **224 passed** |
| Frontend type-check | Pass |
| Frontend unit tests | **59 passed** (5 files; was 45) |
| Frontend build | Pass |
| Browser end-to-end | **8 passed** |

New unit tests cover: one current menu section per page (5 routes), "planned" labels, date and number formatting, readable flag labels, geometry wording, the form error clearing, no pointless retry, finding-count wording, and results opening the requested run. Two existing tests and two browser-test assertions were updated because they checked wording that was deliberately changed.

The review walk-through itself is **not** a test and is not part of `check.ps1`. It passed (completed all steps) on its final run.

## 6. Manual checks performed

- The full user journey with the real model on the GPU: open project → upload photograph → preview appears → run analysis → job goes Queued → Needs review → open results → boxes drawn over the photograph → hover highlights the matching box.
- Upload states: stored, duplicate, wrong content for the name, corrupt, unsupported type, long file name, raster, video.
- Job states: queued, checking inputs, processing with measured progress, failed with a code, cancelled, completed with warnings, completed with no findings; retry offered only where it can help; cancel offered only while a job is active.
- Model unavailable and worker not running (injected).
- API unreachable (injected): the header badge turns red and the dashboard shows a clear error with its code.
- Every screen at desktop and phone width.
- Text overflow with a 92-character file name.
- Console errors and failed requests (section 3).

Not done: keyboard-only navigation, screen reader, automated contrast checking, other browsers than Edge, high-DPI scaling, very large numbers of files or jobs.

## 7. Screens that are still placeholders

Each says "Not available yet — planned for Phase N" and is marked "planned" in the menu:

| Screen | Planned phase |
|---|---|
| Map & Analysis | 4 |
| Observations (review) | 4 |
| Reports | 5 |
| 3D View | 6 |
| Satellite/Aerial Analysis | not scheduled — no raster model approved |
| Video Analysis | not scheduled — no video model approved |

Also not present: confirming, rejecting or relabelling findings (Phase 4); a cross-project activity list; editing a project's location after creation.

## 8. How to repeat the review

```powershell
. .\scripts\env.ps1
.\scripts\dev.ps1                       # database, API, worker and web app; open http://127.0.0.1:5173
```

Then, by hand:

1. Create a profile, then a project (try a location with "WGS84 + LCC", then with `EPSG:32643`).
2. Upload two or three photographs, the same photograph twice, a PNG renamed to `.jpg`, and a `.gif`.
3. Open a photograph, wait for its preview, choose the model and press **Run analysis**.
4. Watch the job reach "Needs review", then press **View results**. Hover the rows in the table.
5. Upload a GeoTIFF and a short MP4; open each.
6. Visit Settings → Models and Diagnostics.
7. Make the browser window narrow (about phone width) and repeat steps 3–4.

Screenshots of all of this, taken on 2026-10-12, are in `docs/ui-review/screenshots/` for comparison.

To regenerate the screenshots automatically against the running app:

```powershell
cd apps\web
pnpm exec playwright test --config playwright.review.config.ts
```

It needs the input files in `D:\geopoc-cache\review` (or set `GEOPOC_REVIEW_FILES`).

To start again from an empty application (deletes all projects, files and findings in the working database; keeps model weights):

```powershell
.\scripts\reset-working-data.ps1
```

To stop everything except the database: `.\scripts\stop.ps1`.

## 9. State left behind

- The application is **running** (`scripts\dev.ps1`) with the review project and files in the working database, so you can look at them directly. Run `scripts\stop.ps1` to stop it.
- Nothing is committed. Phase 3 and Phase 3.5 changes are both in the working tree.
- The screenshots (49 files, 7.3 MB) are in the working tree under `docs/ui-review/`.
