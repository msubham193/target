# Phase 3 Report — Observation Contract, Job Worker, Previews, GPU Feasibility

Date: 2026-10-12. Phase 3 is complete at its gate for the approved scope. Phase 4 has not been started.

**Scope delivered:** the canonical observation contract; database-backed jobs with a separate worker; previews; GPU feasibility; and the first ground-image pipeline. Overhead-raster and video inference were out of scope and were not built, so the specification's Phase 3 exit criterion ("each pipeline independently returns structured results") is met for **ground images only**.

The Phase 2 baseline was committed first as `f51021f`. **Phase 3 changes are in the working tree and are not committed.**

## 1. Checkpoint before changes

| Check | Result |
|---|---|
| Phase 1 commit `17c37e9` exists | Verified |
| Migration `0002` is the database head; its four tables exist | Verified |
| 54 files to commit; no `.env`, uploads, database files, caches or weights among them; real password in none | Verified |
| `scripts\check.ps1` before committing | All 7 passed (109 backend, 29 frontend, 6 browser) |
| Phase 2 committed | `f51021f`, clean tree |

## 2. What was built

| Area | Delivered |
|---|---|
| Observation contract | V3-based schema as strict validated models; controlled flag registry; six review states with 18 permitted transitions; V2-to-V3 converter. Documented in [architecture/observation-schema.md](architecture/observation-schema.md). |
| Jobs | `job` table as the queue; lifecycle with the nine specified states; progress, stage, log; automatic retry of transient failures; manual retry; drafts; cancellation |
| Worker | Separate process, same codebase; one child process per job; heartbeat; recovery of jobs left by a crashed or restarted worker; reports its capabilities |
| Previews | Automatic on upload; images, rasters and video; two sizes; memory-bounded; originals untouched |
| Ground-image pipeline | Model loaded from the manifest; inference; every output validated against the contract; run provenance; observations stored |
| GPU | CUDA PyTorch installed and verified; one model selected, installed and run. See [gpu-feasibility.md](gpu-feasibility.md). |
| API | Jobs (create, list, get, cancel, retry, start), runs, observations, preview, model health |
| UI | Preview on the asset page and in the asset list; job panel with status, progress, error, cancel and retry; model picker with availability; Ground Image Analysis screen with boxes drawn over the image |

## 3. Migrations

`0003_phase3_jobs_previews_observations` (revises `0002`): `job`, `job_event`, `worker_heartbeat`, `artifact`, `pipeline_run`, `observation`. Constraints: only the nine job states; only the six review states; every stored observation has at least one evidence entry; a derived file can never be registered among the originals. Applied to the working database (`0003 (head)`); tested up, down and up.

## 4. Dependency changes

| Change | Detail |
|---|---|
| `torch==2.11.0+cu128`, `torchvision==0.26.0+cu128` | New optional group `ml`, from the official PyTorch index. Verified on the GPU; allowed by Smart App Control. |
| Python environment location | Moved to `D:\geopoc-cache\venv-api` (4.4 GB); the old 263 MB environment on C: was removed |
| Model weights | 167.1 MB at `D:\geopoc-data\weights\…`, outside the repository |

No frontend dependency changed. OpenCV is still not installed. No system-level change.

## 5. Test results

Final run of `scripts\check.ps1` on 2026-10-12:

| Check | Result |
|---|---|
| Backend lint and format | Pass |
| Backend tests | **224 passed**, 0 failed, 0 skipped |
| Frontend type-check | Pass |
| Frontend unit tests | **45 passed** (4 files) |
| Frontend build | Pass |
| Browser end-to-end | **8 passed** |

### Required test areas — automated

| Area | Result | How it was tested |
|---|---|---|
| All six review states and permitted transitions | Pass | Every one of the 36 state pairs is checked for both actors; rules on who, notes and reopening |
| Evidence-reference validation | Pass | Zero evidence, wrong asset, missing region, video without frame or timestamp; plus a database constraint |
| Pixel-coordinate conventions | Pass | Convention fields required; 10 invalid boxes refused; edge-touching and fractional boxes accepted; scaling back from a reduced inference image |
| Pixels never become coordinates | Pass | Ground and video observations with a geometry are refused; no derivation method accepts pixels; incomplete CRS identifiers refused |
| V2-to-V3 field mapping | Pass | All 21 V2 fields accounted for, none dropped; the specification's own V2 example converts; unknown V2 fields refused |
| Job persistence and state transitions | Pass | Queue, claim, run, finish; survives discarded connections; drafts; two workers never share a job |
| Worker errors and retries | Pass | Deterministic, transient, attempt limit, unexpected error; a real child-process crash retried by a real worker |
| Cancellation | Pass | Before start; cooperative while running; a real worker ending a job that ignores the request |
| **Process restart** | Pass, performed for real | A real worker was killed mid-job; the job stayed marked as running; a second worker recovered and completed it |
| Preview generation | Pass | JPEG, PNG, WebP; 8-bit, 16-bit, signed and float rasters, with NaN, without CRS; MP4, MOV, MKV; transparency; orientation; regeneration |
| Unsupported preview formats | Pass | Complex-valued raster: clear failure, asset unharmed, not retried |
| **Large-file memory-bounded processing** | Pass, performed for real | A 400 MB raster previewed in a fresh process; the operating system's peak-memory counter stayed under 150 MB above baseline |
| **CUDA availability** | Pass, performed on the RTX 5060 | A real tensor operation; the build includes the card's architecture |
| **Selected model's inference path** | Pass, performed on the GPU | Offline load from checksummed weights; inference; through a job; contract-valid output |
| API and frontend job/preview integration | Pass | API tests; 16 component tests; a browser test with a real worker |
| API contract | Pass | Generated requests against all 18 documented paths |

### Manual checks (not part of the automated suite)

| Check | Result |
|---|---|
| GPU probe in a throwaway environment before installing: tensor operation, cuDNN convolution, GPU NMS | Passed |
| One real photograph through real API and worker processes on the GPU | Job finished in 5.4 s; GPU memory 1,503 → 2,328 → 1,499 MB; 2 findings stored, both `needs_review`, no geometry |

### What was not tested

- **Model accuracy.** No labelled or representative data exists. The automated GPU tests use a synthetic image; the manual check used one photograph.
- **The pipeline tests use a stub detector.** They prove plumbing and contract enforcement, not model behaviour.
- **Qwen**, alone or alongside a vision model.
- **Large images or video previews near the size limits.** The large-file test covers a 400 MB raster only.
- **Cancelling a real GPU inference mid-run.** Cancellation was tested on diagnostic jobs.
- **The `cu130` PyTorch build.**
- **Database failure during a job**, beyond the unit-level retry logic.
- A backend static type-checker and accessibility checks remain unconfigured.

Specification tests: T-08 (uncalibrated score: semantics disclosed, no probability claim) now passes for ground images. T-01 still passes. No other T-test is newly claimed.

## 6. Changed files

**New (backend):** `geopoc/contracts/{flags,review,observation,v2}.py`; `geopoc/jobs/{queue,handlers,run_job,worker,probe}.py`; `geopoc/previews/generate.py`; `geopoc/pipelines/{base,ground_image}.py`; `geopoc/pipelines/adapters/{stub,torchvision_detection}.py`; `geopoc/api/routers/jobs.py`; `migrations/versions/0003_…py`; `tests/{test_contracts,test_jobs,test_previews,test_ground_pipeline,test_gpu_model}.py`; `tests/fixtures/manifest.stub.yaml`.

**Changed (backend):** `pyproject.toml`, `uv.lock`, `geopoc/config.py`, `geopoc/devtools.py`, `geopoc/db/models.py`, `geopoc/registry/manifest.py`, `geopoc/ingestion/service.py`, `geopoc/api/{main,schemas}.py`, `geopoc/api/routers/{assets,health}.py`, `openapi.json`, `tests/{conftest,test_health,test_migrations}.py`.

**New (frontend):** `src/components/{AssetPreview,JobsPanel}.tsx`, `src/pages/GroundAnalysisPage.tsx`, `src/pages/jobs.test.tsx`, `src/lib/jobs.ts`, `e2e/phase3.spec.ts`.

**Changed (frontend):** `src/App.tsx`, `src/api/{client.ts,schema.d.ts}`, `src/pages/{AssetDetailPage,AssetsPage,SettingsPage,LoginPage}.tsx`, `src/pages/ingestion.test.tsx`, `playwright.config.ts`, `e2e/phase1.spec.ts`.

**Other:** `models/manifest.yaml`, `models/cards/fasterrcnn_resnet50_fpn_v2.md`, `scripts/{env,dev}.ps1`, `.env.example`, `README.md`.

**Docs:** new `docs/architecture/observation-schema.md`, `docs/jobs-and-previews.md`, `docs/gpu-feasibility.md`, `docs/phase-3-report.md`; updated `docs/architecture/decision-log.md` and `observation-schema-notes.md`. The specification DOCX is unchanged.

## 7. Known limitations

1. **Only ground photographs can be analysed.** Raster and video analysis are refused, not simulated.
2. **The model is unevaluated.** It knows 80 everyday classes. Every finding starts as `needs_review`.
3. **The 0.5 score cutoff is an operating setting**, not a validated threshold. Detections below it are counted, not stored.
4. **Findings cannot yet be confirmed, rejected or relabelled** through the application. The state rules exist as tested logic; the review API, history and audit are Phase 4.
5. **No conflict or duplicate-finding detection.** Each analysis run stands alone; running twice gives two independent sets of findings.
6. **Findings from a duplicate asset are not linked** to those of its original.
7. **One job at a time**, and the model reloads for every analysis job.
8. **Stale-job recovery needs a running worker.**
9. **No blur or quality gate before inference.** The sharpness indicator is recorded at ingestion but has no validated threshold, so it does not route anything to review (U-005).
10. **Observations have no geometry column yet**; ground-image findings never have a location anyway.
11. **The environment is on D:.** Run `. .\scripts\env.ps1` first in any new terminal, or `uv` will build a second, model-less environment in the project folder.
12. **Local profiles are still not authentication**; all services still bind to loopback only.

## 8. Unresolved issues

| Issue | Needs |
|---|---|
| Two judgement calls in the schema: `summary` merged into `claim.description`; V2 `unresolved` mapped to `needs_review` | Your confirmation, or say which to change |
| Labelled, representative imagery for evaluation and calibration | From you (U-004, U-005) |
| Whether the model's COCO-trained weights suit the intended use beyond a PoC | A licence decision (U-018) |
| Overhead and video pipelines: models and scope | Decisions (U-001, U-010) |
| Qwen model for aggregation, and its VRAM behaviour next to vision jobs | Phase 5 (U-002) |
| Version 2.0 / 3.0 labels inside the specification | Still recorded as unresolved |
| `D:\geopoc\docs` leftover copy from Phase 1 | Yours to delete |
