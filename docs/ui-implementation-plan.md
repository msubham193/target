# UI Implementation Plan — UI/UX Foundation Phase

Date: 2026-10-17. Scope: the interface only, over the existing Phase 1–3 backend. No Phase 4 work.

## Starting point (inspected)

- Uncommitted Phase 3 and 3.5 work: 72 entries. Preserved; nothing reset. A copy of the frontend source was taken to `D:\geopoc-cache\backups\web-src-20261017-100248` before changes.
- Working features to keep: local profile entry, projects (create, search, edit, archive, CRS-validated location), upload with signature/readability checks and duplicates, asset list and detail, previews, background jobs with cancel and retry, ground-image analysis with the real model, results with boxes.
- Placeholders today: Map, Observations, Reports, 3D, satellite and video analysis.
- Authentication: there is none. A "profile" is a name recorded in the audit history. The Login screen must not pretend otherwise: no password field.

## Decisions

| Topic | Decision |
|---|---|
| Component library | None added. A small design system is written in `src/components/ui.tsx` on top of Tailwind. |
| Typography | Poppins from `@fontsource/poppins` (bundled locally, OFL-1.1; nothing fetched at run time), falling back to system fonts. |
| Theme | Dark charcoal surfaces, one blue accent, colour tokens defined once in `index.css`. |
| Icons | A small set of inline SVG icons in `src/components/icons.tsx`; no icon package. |
| Backend | Two additive, read-only endpoints so the dashboard can show real figures instead of invented ones: `GET /api/v1/projects/{id}/summary` (asset counts by modality, job and observation counts, last activity) and `GET /api/v1/jobs` (recent jobs across projects). No existing endpoint, migration or behaviour changes. |
| Project context | Project-scoped screens live under `/projects/:id/...`. The sidebar remembers the last opened project; with none, those items are disabled with a reason. |

## Screen-to-component mapping

| # | Screen | Route | Layout | Main components | Data (all real) | Planned parts, labelled as such |
|---|---|---|---|---|---|---|
| 1 | Login | `/login` | Auth layout, no sidebar | `AuthLayout`, profile list, create-profile form, service status | profiles, `/health` | — (no password: none exists) |
| 2 | Project Dashboard | `/dashboard` | Shell; summary strip, project table, recent jobs | `PageHeader`, `ProjectTable`, `RecentJobs` | projects, project summaries, recent jobs | — |
| 3 | New Project & Location | `/projects/new` | Two-column form with an explanatory side panel | `Field`, `CrsPicker`, AOI inputs | CRS search, create project | Place search (needs an online geocoder) |
| 4 | Data Upload & Ingestion | `/projects/:id/assets/new`, `/assets`, `/assets/:assetId` | Tabs: Upload / Assets; detail page | drop zone, upload queue, asset table, filters, `AssetPreview`, `JobsPanel` | upload API, assets, previews, jobs | — |
| 5 | Map & Analysis | `/projects/:id/map` | Three-pane `Workspace`: layers, map area, info panel | `Workspace`, layer list, `PlannedArea` | assets with their real georeferencing status and extents (as text) | The map itself: no map engine is installed, so nothing is drawn |
| 6 | Ground Image Analysis | `/projects/:id/ground`, `/assets/:assetId/ground` | `Workspace`: photo list, image viewer, detections and provenance | `ImageViewer` (zoom, fit), detection overlay, detections list, run panel | assets, jobs, runs, observations | Review actions (Phase 4) |
| 7 | Satellite Image Analysis | `/projects/:id/satellite`, `/assets/:assetId/overhead` | `Workspace`: raster list, image-space preview, metadata | `ImageViewer`, georeferencing panel | raster metadata, preview | Raster analysis; band and layer controls |
| 8 | Video Analysis | `/projects/:id/video`, `/assets/:assetId/video` | `Workspace`: video list, player with frame stepping, metadata and results | native `<video>` from the stored original, frame step from the real frame rate | video metadata, original bytes | Frame-level analysis, timeline of detections |
| 9 | 3D View | `/projects/:id/3d` | Viewport with side panel | `PlannedArea`, disabled control list | what data the project actually has | Everything 3D |
| 10 | Observations | `/projects/:id/observations` | Filter bar, table, detail panel | filters, observation table, detail panel | stored observations, assets | Review actions (Phase 4) |
| 11 | Reports | `/projects/:id/reports` | Summary, contents, exports | project summary, evidence counts, export list | project summary, observations | Report generation, PDF/HTML |
| 12 | Settings | `/settings/:tab` | Sectioned page with side tabs | sections: Models, Coordinate systems, Data sources & storage, Offline & network, Preferences & diagnostics | model health, CRS registry, health | Custom CRS registration, online sources |

Shared: `AppShell` (collapsible sidebar with the four navigation groups, project selector, top bar with breadcrumbs and status), `Workspace` (three-pane analysis layout, stacks on narrow screens), `ImageViewer`, `PlannedArea`/`PlannedBadge`, `EmptyState`, `LoadingState`, `ErrorState`, `StatusBadge`.

## Verification plan

`scripts\check.ps1`; updated unit and browser tests; the review walk-through at 1366 px and 390 px with screenshots in `docs/ui-review/screenshots-ui-foundation/` (not committed); manual inspection of every screenshot; console and network log.
