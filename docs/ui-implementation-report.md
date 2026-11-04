# UI/UX foundation phase — implementation report

Date: 2026-10-17. Status: complete and awaiting review. Phase 4 has not been started. Nothing has been committed.

The plan and the screen-to-component mapping are in [ui-implementation-plan.md](ui-implementation-plan.md).

## 1. Design system and shared components

**Approach.** The design system is built on the existing stack: React 19, TypeScript, Tailwind v4 and TanStack Query. No component library was added.

**Design tokens.** They are defined in `apps/web/src/index.css` under `@theme`:

- Surfaces: dark charcoal, from `canvas` #0e1014 through `surface` and `raised` to `overlay`.
- Lines: `line` and `line-strong`.
- Text: `fg`, `fg-muted` and `fg-subtle`.
- Accent: one restrained blue, `accent` #5b9bff, plus `accent-strong` and `accent-soft`.
- Status colours: `ok`, `warn` and `danger`, each with a soft variant.
- Radii and one panel shadow.

**Typography and motion.**

- Poppins 400/500/600 is bundled locally through `@fontsource/poppins`, so there are no font requests to Google.
- The base size is 14 px.
- All focusable elements share one `:focus-visible` outline in the accent colour.
- `prefers-reduced-motion` is respected.

**Shared components.**

| Area | Components | File |
| --- | --- | --- |
| Actions | `Button` (primary, secondary, ghost, danger; sm, md), `LinkButton`, `IconButton` (an accessible label is required), `TextLink` | `components/ui.tsx` |
| Forms | `Field` (label, hint, error with `role=alert`), `inputClass`, `selectClass` | `components/ui.tsx` |
| Layout | `PageHeader`, `Panel`, `Workspace` (three panes that stack on narrow screens), `FileList` | `ui.tsx`, `Workspace.tsx` |
| Status | `Badge` (neutral, accent, ok, warn, danger, planned), `PlannedBadge`, `Notice` (info, warning, error, success), `ErrorNotice` (shows the API error code and request ID) | `ui.tsx` |
| States | `Loading`, `Skeleton`, `EmptyState` (can act as the page heading on not-found pages), `PlannedArea` | `ui.tsx` |
| Data | `DescriptionList`, table classes, `TableFrame` (scrolls horizontally) | `ui.tsx` |
| Navigation | `Breadcrumbs`, `Tabs` (arrow-key navigation), `Tooltip`, `Dialog` (native `<dialog>`), `Kbd` | `ui.tsx` |
| Media | `ImageViewer` (zoom out/in, fit, actual size, Ctrl+wheel zoom, an overlay drawn in source pixels), `AssetPreview`, `AssetThumb` | `ImageViewer.tsx`, `AssetPreview.tsx` |
| Jobs | `JobsPanel`, `RunAnalysis`, `JobStatusBadge` | `JobsPanel.tsx` |
| Icons | Hand-drawn inline SVG set | `icons.tsx` |

**Shell (`components/AppShell.tsx`).**

- **Sidebar.** Collapsible, and the choice is remembered in this browser. Navigation is grouped as follows:

  | Group | Screens |
  | --- | --- |
  | Workspace | Project Dashboard, New Project & Location |
  | Data | Upload & Ingestion, Map & Analysis (planned) |
  | Analysis | Ground, Satellite, Video, 3D (planned), Observations, Reports |
  | Configuration | Settings |

- **Project selector.** It sits in the sidebar. It remembers the last project and forgets one that no longer exists. Screens that need a project are shown as disabled, with the reason "open a project first".
- **Top bar.**
  - The current project.
  - A status badge: Services OK, Worker not running, API unreachable, or Service problem.
  - "Local only".
  - The profile name, with a "Switch profile" button.
- **Breadcrumbs.** Every screen inside a project shows them.
- **Narrow screens.** The sidebar becomes a modal drawer. Focus moves to the close button when it opens, Escape closes it, and the backdrop closes it on click.
- **Skip link.** "Skip to content" is the first Tab stop.
- **Login.** It uses its own layout, without the shell.

## 2. Status of the 12 screens

| # | Screen | Route | Status | Notes |
| --- | --- | --- | --- | --- |
| 1 | Login | `/login` | Working | Choose an existing local profile or create one; the name is validated, and a duplicate name is explained. There is no password. The page says plainly that a profile "is not a login and gives no access control". Service health (database, storage, worker) is shown and re-checked every 5 s while anything is not OK. |
| 2 | Project Dashboard | `/dashboard` | Working | Project search, tag filter and "show archived". Per-project counts come from the new `GET /projects/{id}/summary`; a skeleton shows while they load, and nothing is estimated. "Recent jobs" comes from the new `GET /jobs`. Empty and error states are included. |
| 3 | New Project & Location Setup | `/projects/new` | Working | A location requires a CRS from the local PROJ registry. "WGS84 + LCC" finds no match. A side panel explains that a project location is not a file's footprint. The overview and map show the location as entered, and separately as transformed to EPSG:4326. Place search is disabled because it needs an online source. |
| 3b | Project overview | `/projects/:id` | Working | Counts, location as entered and transformed, edit, archive and restore. |
| 4 | Data Upload & Ingestion | `/projects/:id/assets/new`, `/assets`, `/assets/:assetId` | Working | Existing upload API and logic unchanged. Shows the per-kind rules, a drop zone, a queue with each file's outcome, and the asset list with filters and thumbnails. Asset detail has preview, jobs, metadata and "Download original". |
| 5 | Map & Analysis View | `/projects/:id/map` | **Planned** | No map, tiles or footprints are drawn. The layer list separates files that could be placed (georeferenced rasters only) from those that could not, with the reason. The project location is shown as text. |
| 6 | Ground Image Analysis | `/projects/:id/ground`, `/assets/:assetId/ground` | Working | Viewer-first layout with zoom, fit and actual size. Boxes are drawn in source pixels and can be clicked to select. You can run an analysis and choose between runs. The findings table shows the raw score and its calibration status in separate columns. Selecting a finding shows its details. "How this was produced" shows the model, score meaning, cutoff ("an operating setting, not a validated threshold"), device and run. Advanced details are collapsible. Review actions are labelled "Planned for Phase 4" and have no buttons. |
| 7 | Satellite Image Analysis | `/projects/:id/satellite`, `/assets/:assetId/overhead` | Partly working | Raster preview in image space (labelled as not north-up). Shows georeferencing status, CRS, geotransform, extent in the native CRS and EPSG:4326, bands and unknowns. Raster analysis, band/contrast controls and layers are **planned**. |
| 8 | Video Analysis | `/projects/:id/video`, `/assets/:assetId/video` | Partly working | Native playback of the stored original. Previous/next frame steps use the average frame rate; the frame number is labelled as an estimate. Shows metadata and unknowns. If the browser cannot play the file, it says so and shows the preview frame. Frame-level analysis and a results timeline are **planned**; no detections or timestamps are shown. |
| 9 | 3D View | `/projects/:id/3d` | **Planned** | "No compatible 3D data". The scene controls are listed as planned, and the vertical datum is stated as unknown. No terrain is drawn. |
| 10 | Observations | `/projects/:id/observations` | Working (read-only) | Filters by class/file text, kind of input and review state. Each observation shows its claim, evidence (pixel region), provenance and checksum, with a link to Ground Image Analysis. The six states are listed with their meanings. There are no review buttons: the API has no review endpoint. |
| 11 | Reports | `/projects/:id/reports` | Partly working | Project context comes from the summary endpoint. "Observation records (JSON)" downloads the stored records in the browser; nothing new is computed. "Original files" can be downloaded from each file's page. PDF, HTML and spatial-layer exports, report generation and report history are **planned**. |
| 12 | Settings | `/settings/models`, `crs`, `sources`, `network`, `diagnostics` | Working (mostly read-only) | Models: availability, score meaning, calibration and licence from the manifest. CRS: registry search and the DSM position. Data sources & storage. Offline & network: read from `.env`; states that the app does not block the network at OS level. Diagnostics. The only editable settings are the two browser-local preferences (sidebar collapsed; forget last project). Every read-only panel is marked "Read-only". |

Edge pages ("This project does not exist" and "Page not found") are styled empty states that act as the page heading.

## 3. Functionality preserved

- **Upload.** The endpoints, client-side checks, queue behaviour, duplicate accept/link/flag, signature-mismatch and unreadable-file rejection, per-kind limits, checksums, read-only originals and storage rules are all unchanged. The walkthrough confirmed that the downloaded original's SHA-256 matches the uploaded bytes (`original_sha256_matches: true`), and e2e `phase2` checks the same.
- **Jobs.** Previews, analysis, cancel, retry, the no-retry rule for unsupported previews and the worker-not-running state are all unchanged.
- **Ground analysis.** It ran the real `fasterrcnn_resnet50_fpn_v2@coco_v1` on the GPU during the walkthrough: 2 findings stored, 298 below the cutoff. E2E `phase3` covers it with the stub detector.
- **Backend.** Migrations, contracts and job processing are unchanged. Two endpoints were added, both read-only:
  - `GET /api/v1/projects/{id}/summary`: counts by kind, job status and review state, plus last activity.
  - `GET /api/v1/jobs?limit=&status=`: recent preview and analysis jobs across projects.

  Both are covered by `tests/test_summaries.py`, and `openapi.json` and `schema.d.ts` were regenerated.

## 4. New interactions that work

- **Shell:** collapsible sidebar with grouped navigation and a project selector; mobile drawer (focus, Escape, backdrop); skip link; health badge.
- **Login:** name validation and the duplicate-name message.
- **Dashboard:** per-project counts and recent jobs.
- **Ground Image Analysis:**
  - Zoom, fit and actual size.
  - Selecting a finding from the table or a box, with keyboard Enter/Space on rows.
  - The run selector.
  - Collapsible technical details.
- **Satellite:** switching between rasters in the side list.
- **Video:** frame stepping. Measured in the walkthrough: two steps brought `currentTime` to 0.08 s at 25 fps, with no media error.
- **Observations:** filters and the detail panel.
- **Reports:** JSON export. The walkthrough downloaded `observations-<project>.json` with 2 records.
- **Settings:** section navigation and browser-local preferences.
- **Health:** the login and model-health views re-check every 5 s while the worker or a service is down.

## 5. Features still labelled planned

| Screen | Planned items |
| --- | --- |
| Map & Analysis | Map engine, basemap, layers, footprints |
| 3D View | Viewer and all scene controls |
| Satellite | Raster analysis, band and contrast controls, layers |
| Video | Frame-level analysis and a timeline of findings |
| Review | All review actions (confirm, reject, mark inconclusive, dispute, relabel), on Ground Image Analysis and Observations; Phase 4 |
| Reports | Report generation, report history, PDF, HTML and spatial-layer exports |
| Settings | Local language model, custom CRS definitions, online data sources |
| New Project | Place search, online imagery sources |

Planned items carry a dashed "Planned" badge or a `PlannedArea`, and none of them has a control that appears to work.

## 6. Tests and viewport checks

**Full check suite.** `scripts\check.ps1` was run after the final changes, and all 7 checks passed:

| Check | Result |
| --- | --- |
| ruff check | pass |
| ruff format | pass |
| pytest | 229 passed |
| tsc | pass |
| vitest | 78 passed |
| vite build | pass |
| Playwright e2e | 8 passed |

**New and updated unit tests.**

- `lib/format.test.tsx`: main navigation.
  - Exactly one current screen on each of 12 routes.
  - Groups and planned labels.
  - Disabled entries give their reason.
  - The collapsed sidebar persists.
- `pages/screens.test.tsx` (new):
  - Login: no password field, the non-authentication statement, validation and duplicate name.
  - Observations: filters, all six states, detail, no review buttons.
  - Reports: JSON export, disabled when empty, no report download.
  - Settings: network read-only with no toggles, the browser preference, models without accuracy claims.
- `pages/jobs.test.tsx`: ground-analysis tests moved to the new routes.
  - The raw score and calibration are checked as separate cells.
  - Selecting a finding is checked, along with the absence of review buttons.
- `pages/pages.test.tsx`: dashboard counts come only from the summary endpoint; recent jobs.

**Updated e2e specs.** `phase1`–`phase3` now use the new navigation and labels. The obsolete "is not implemented" check was replaced by checking the satellite and video screens with no files and no "Run analysis" button.

**Browser walkthrough (automated, run in Edge).**

- Files: `apps/web/playwright.ui-review.config.ts` and `apps/web/review/ui-foundation.spec.ts`.
- It runs on its own scratch stack (database `geopoc_review`, data folder `D:\geopoc-data\_review`, ports 8011/5175); the working database was not touched.
- It visits all 12 screens plus the project overview, asset list, asset detail and edge pages, at 1440×900 and 390×844.
- Final run: passed, 57 screenshots.
- For every screenshot it audited:
  - horizontal overflow;
  - exactly one `h1`;
  - unnamed buttons and links;
  - form fields without a label;
  - images without `alt`;
  - duplicate ids.

  Result: **0 issues across 57 audited screens**.
- **Console.** The only errors were two expected failed requests:
  - 415 for the deliberately mislabelled `renamed_png.jpg` upload;
  - 404 for the deliberately missing project.
- **Network.** There were no requests to any host other than 127.0.0.1.
- **Log:** `docs/ui-review/ui-foundation-browser-log.json`.

**Manual inspection.** I looked at the screenshots myself. Defects found that way and fixed before the final run:

- The findings table clipped its Review column.
- Narrow-screen overflow on the dashboard came from grids with no base column (fixed on 8 pages).
- The map screen labelled the transformed EPSG:4326 location as "as entered".
- The login footer claimed review works offline.
- The disabled primary button looked enabled.
- There was a duplicate `project-selector` id while the drawer was open.
- The not-found pages had no `h1`.
- Escape did not close the drawer.
- Service-health text ran together ("OK· Database").

## 7. Screenshots

- **This phase:** `docs/ui-review/screenshots-ui-foundation/`, 57 PNGs, about 11 MB.
- **Earlier walkthrough (Phase 3.5):** `docs/ui-review/screenshots/`, 49 PNGs.
- **Walkthrough input files:** `D:\geopoc-cache\review`. This now includes `walkthrough_h264.mp4`, an H.264 re-encode of the test clip made with ffmpeg because Edge cannot play the MPEG-4 Part 2 original.
- **Exported records:** the walkthrough's JSON export is saved as `docs/ui-review/observations-export.json`.

None of these is in `.gitignore`. They are uncommitted, and should stay out of Git unless you approve.

## 8. Remaining defects and accessibility limits

- **Contrast and screen readers.** No screen-reader or colour-contrast audit tool was run; there is no axe-core, to avoid a new dependency. The checks above are structural only. The `fg-subtle` text (#828c9c on #15181e) should be measured.
- **Full-page screenshots.** The sticky sidebar and top bar appear mid-page in tall desktop captures. This is how Playwright stitches full-page screenshots, not a layout fault.
- **Image viewer.** It has no panning by drag; zoomed images scroll inside the frame instead. Ctrl+wheel zoom has no keyboard equivalent beyond the buttons.
- **Video frame step.** It uses the average frame rate, so for variable-frame-rate files the frame number is an estimate (labelled as one).
- **Finding rows.** Rows in the findings and observations tables are focusable `tr` elements with `aria-selected`. This works with a keyboard, but it is not a full ARIA grid.
- **Page widths.** Content width varies by screen type (forms 5xl, lists and settings 6xl, workspaces full width). This is intentional, but reviewers may prefer one width.
- **Worker startup flake.** In one end-to-end run before the health-polling change, the worker took longer than the 30 s test timeout to start. It was not reproduced in the later runs, and the cause is not established.
- **Outdated Phase 3.5 script.** `apps/web/review/capture.spec.ts` targets the old navigation (for example the "Project" nav) and would need updating to run. It was not run in this phase. It is superseded by `ui-foundation.spec.ts` and was kept, not deleted.

## 9. Dependencies changed

- **Added:** `@fontsource/poppins` 5.3.0 (OFL-1.1), a local font files package for the web app.
- **Removed:** none.
- **Backend:** no dependency changes in this phase. The PyTorch `ml` group pins in `pyproject.toml` date from Phase 3.
