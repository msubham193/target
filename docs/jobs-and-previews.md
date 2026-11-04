# Background Jobs, the Worker and Previews

Implemented in Phase 3. Everything runs on this computer. There is no message broker and no distributed queue: one machine and one GPU do not need one.

## How it fits together

```
Browser ──► API process ──► job row in PostgreSQL ◄── Worker process ──► child process per job
              (never runs                                (claims, monitors,     (does the work, then exits,
               models)                                    cancels, recovers)     releasing memory and VRAM)
```

- The **API** only inserts a job row and returns. It never generates previews or runs models.
- The **worker** (`python -m geopoc.jobs.worker`) shares the codebase. It claims the oldest runnable job with `SELECT … FOR UPDATE SKIP LOCKED`, so several workers could never take the same job.
- Each job runs in its **own child process**. That is what makes cancellation dependable and guarantees that whatever a job loaded, including GPU memory, is freed when it ends.
- `scripts\dev.ps1` starts the worker alongside the API. Without a worker, jobs stay queued and the UI says so.

## Job lifecycle

The states are those of the specification (V2 §9).

| State | Meaning |
|---|---|
| `draft` | Saved, not submitted |
| `queued` | Waiting for the worker; can be cancelled at once |
| `validating` | Claimed; inputs are being checked before real work starts |
| `processing` | Working; shows the stage, and progress when it can be measured |
| `needs_review` | Finished; its output needs human review |
| `completed` | Finished; a job that finds nothing also ends here |
| `completed_with_warnings` | Finished, but some model output failed validation and was not stored |
| `failed` | Ended with an error; shows a message and a diagnostic code |
| `cancelled` | Stopped on request; states whether any output exists |

Progress is a number only when it can be measured. Otherwise it is null and the UI shows the stage instead of inventing a figure.

### Errors and retries

| Kind of failure | Examples | What happens |
|---|---|---|
| Deterministic | Unsupported preview format, missing original, model failure on an image | Fails immediately. Retrying would fail the same way. |
| Transient | Storage momentarily unavailable, GPU out of memory | Requeued with a growing delay until attempts run out |
| The job's process dies | Crash, killed process | Detected by the worker; retried as transient (`WORKER_CHILD_CRASHED`) |
| The worker itself dies or is restarted | Crash, reboot, closed window | The job is left marked as running; the next worker notices its heartbeat has stopped and requeues it (`WORKER_LOST`) |
| Unexpected error | A bug | Fails with `INTERNAL_ERROR`; details go to the worker log, not to the user |

Attempt limits: 3 for previews, 2 for analysis. A manual **Retry** creates a new job linked to the old one; the old job and its log are kept.

### Cancellation

- A job that has not started is cancelled immediately and never runs.
- A running job notices the request at its next checkpoint and stops.
- If it does not stop within 5 seconds, the worker ends its process.
- A job writes its results in a single transaction at the very end, so a cancelled job leaves **no** partial output. The job record says so.

### Durability

A job exists only as a database row, so it survives a restart of the API, the worker or the computer. This was tested by killing a worker mid-job and starting another, which finished the job.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/projects/{id}/jobs` | Queue a `preview` or `analyze` job on an asset (`submit: false` saves a draft) |
| `GET /api/v1/projects/{id}/jobs` | List jobs; filter by asset, kind, status |
| `GET /api/v1/jobs/{id}` | State, stage, progress, error, result and the last 50 log lines |
| `POST /api/v1/jobs/{id}/cancel` · `/retry` · `/start` | Cancel; rerun a failed or cancelled job; submit a draft |
| `GET /api/v1/runs/{id}` | How an analysis was produced: model, version, weights checksum, settings, device, timings |
| `GET /api/v1/projects/{id}/observations` | Stored observations in the canonical contract |
| `GET /api/v1/assets/{id}/preview?variant=view\|thumb` | The preview picture |
| `GET /health/models` | Models in the manifest, whether each can run, and what the worker reports about itself |

Analysis can be queued only for the ground-image pipeline. Requests for overhead or video analysis are refused with `PIPELINE_NOT_AVAILABLE`; nothing pretends to work.

## Previews

A preview job is queued automatically when a file is uploaded. The upload does not wait for it.

| Input | How the preview is made | Memory bound |
|---|---|---|
| Ground photograph | Decoded with Pillow and reduced | JPEG is decoded directly at reduced scale. Other formats are fully decoded, bounded by the 100-megapixel ingestion cap. |
| Raster | A decimated read straight to preview size | The full raster is never in memory. GDAL's block cache is capped at 64 MB. Rasters above 64 million pixels are sampled rather than averaged. |
| Video | One frame, scaled by FFmpeg while decoding | One frame |

Two sizes are stored as WebP: `view` (longest side 1,280 px) and `thumb` (256 px). Small images are not enlarged.

What a preview is and is not:

- It is a **derived file** under `<DATA_ROOT>/previews/`. The original is opened read-only and is never changed. A database constraint prevents a derived file from being registered among the originals.
- It keeps the **original's pixel orientation**. EXIF orientation is not applied, so detection boxes line up; the orientation is reported as metadata instead.
- A raster preview is **image space only** and carries no georeferencing. For rasters that are not 8-bit, brightness is stretched between the 2nd and 98th percentile for viewing; the stretch used is recorded. The preview is not the measured values.
- A video preview is **one frame**, not a summary of the video.
- Regenerating replaces the previous preview. While a new one is pending, the existing one is still served.

### When a preview cannot be made

Rasters with complex-valued pixels cannot be shown as a picture. The preview job fails with `PREVIEW_UNSUPPORTED`, the asset stays valid and usable, and the UI says no preview is available and why. `GET …/preview` returns 404 `PREVIEW_NOT_AVAILABLE` in that case and 409 `PREVIEW_NOT_READY` while a preview is still being generated.

## Limits

- One job at a time. There is no parallelism, including for previews, which do not need the GPU.
- The model is loaded afresh for every analysis job (about 0.6 s here). That is the price of releasing VRAM after each job.
- A cancelled or failed job's temporary files are cleaned up by the job itself; a killed process may leave a temporary video frame in `tmp/uploads`, removed at the next API start once it is a day old.
- Stale-job recovery needs a running worker. With no worker, a job left by a crash stays marked as running.
- Job logs are kept indefinitely; there is no pruning.
