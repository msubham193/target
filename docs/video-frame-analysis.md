# Video-frame analysis

Status (2026-10-29): implemented and tested on scratch databases. **No migration is needed** (section 5), so nothing was applied
to the working database, and nothing in this feature has been run against it. The running API and web builds are older; a restart
is needed to see this (not done).

A person chooses a video, a model and a sampling interval, sees how many frames that means, and presses a button. A background
job takes that bounded sample of frames with the local ffmpeg, runs the existing local detector on each frame, and stores the
frames and the findings. Everything runs on this computer. Frames are never sent to any service, and no network option is used by
the job or by ffmpeg.

## 1. What it is not

* **No tracking.** Frames are analysed one by one. A finding in one frame says nothing about any other frame, and the same class
  in two frames is not claimed to be the same object. No link or conflict is ever created between video findings (the
  ground-image pipeline's "possible duplicate" linking is deliberately not called for video; a test checks it).
* **Not complete coverage.** Only the sampled frames were looked at. An event that starts and ends between two samples, or that
  lasts less than the interval, is never seen. Every result screen and the API say so.
* **Not calibrated.** Scores are the model's own values. No threshold has been validated, so every finding starts as `needs_review`.
* **Not located.** A video finding has no geographic location; no camera pose or footprint is inferred.

## 2. Sampling policy

Defined in `geopoc/video/sampling.py` (pure functions, tested without any video).

* **Time axis.** Media time relative to the first presented frame (the first frame is at 0 s), whatever offset the container's own
  timestamps start at. This is the clock a player shows. The container's own time is kept as `container_pts_s` on each frame.
* **Wanted times.** One every `interval_s` from `start_s` (default 0) to `end_s` (default: the last frame).
* **Which frame.** For each wanted time, the frame on screen at that time: the last frame whose presentation time is at or before
  it (before the first frame, the first frame). Several wanted times on one frame (a slow video, or a short interval) analyse
  that frame once; the repeats are listed on it (`also_stands_for_ms`) and counted on the run, not hidden.
* **Bounds.** `interval_s` between a configured minimum (default 0.2 s) and 3600 s; `max_frames` between 1 and a configured ceiling
  (default 300). If the wanted times exceed `max_frames` the request is **refused** with the numbers and what to change; nothing is
  dropped to make it fit. Settings: `GEOPOC_VIDEO_ANALYSIS_MAX_FRAMES`, `GEOPOC_VIDEO_ANALYSIS_MIN_INTERVAL_S` (they can be lowered
  or raised by the owner; the schema caps `max_frames` at 300).
* **Defaults in the form** (5 s, 60 frames) are only starting values; the person always sees the count and starts it explicitly.

### Frame numbers and timestamps

* **Frame number** = the frame's 0-based position in presentation order among all frames of the first video stream. It is computed
  from the presentation time of every packet (an `ffprobe` metadata scan: no decoding, and the video is never read into this
  process's memory). B-frame storage order, a start offset, and variable frame rates are handled by sorting the real times; **no
  frame rate is used to estimate anything**.
* **Timestamp** = that frame's presentation time in whole milliseconds from the first frame (rounded half up).
* If any frame has no presentation time, or two frames share one, the video is **refused** (`VIDEO_TIMING_UNSUPPORTED`) rather than
  numbered by guesswork.
* **Extraction is verified.** Each frame is taken with an accurate seek and the decoder's own report of the delivered frame's time is
  compared with the plan (tolerance 0.6 ms). A different frame is an error (`FRAME_TIMESTAMP_MISMATCH`), never a quietly different
  result. Checked on B-frame MP4, a 2.5 s start-offset MP4, MKV, MOV and 29.97 fps for every frame of the clips.
* The player's seek position is the same media time. A file with an unusual start offset may differ in some players; the stored
  frame image and its checksum, not the player position, are the evidence.

## 3. What is stored (reusing the existing models)

| Thing | Where | Notes |
|---|---|---|
| The run | `pipeline_run`, `pipeline = 'video'` | model name/version/weights checksum, device, ffmpeg version, the sampling policy, frame-numbering and coordinate statements, counts, failed frames with codes |
| Each sampled frame | `artifact`, `kind = 'video_frame'` | the JPEG the model saw (stored under `video_frames/<asset>/<run>/`), its SHA-256, size, dimensions; `details` holds `run_id`, `frame_index`, `timestamp_ms`, `container_pts_s`, `sample_target_ms`, `also_stands_for_ms`, source video SHA-256 |
| Each finding | `observation`, `pipeline = 'video'` | the existing contract: `evidence[0]` has `frame_index`, `timestamp_ms`, the box in the frame's pixels, and `source_excerpt_or_metadata_key = "video_frame_sha256:<hex>"`; `provenance.source_checksum` is the **video's** checksum; `provenance.transformations[0]` records the extraction and the frame's checksum |
| Review | `observation_revision`, audit | unchanged: the same confirm / reject / inconclusive / relabel / note actions, revision history and audit events as for photographs |
| The request | `job` (`kind = 'analyze'`, payload `pipeline: "video"`, `sampling`) and the `job.create` audit event | the chosen sampling is audited |

Box coordinates are in the pixel grid of the extracted frame image (top-left origin, after any rotation the container asks for),
scaled back from the reduced image the model saw. The model, its adapter, box scaling, category naming, contract and review states
are those of the ground-image pipeline: a frame is an image, so the installed image detectors are the models a video analysis may
use (`FRAME_MODEL_PIPELINES`). The detector is not duplicated.

Everything the job produces is written in **one transaction** at the end. A failed, cancelled or retried job leaves no findings,
and the frame files it wrote are removed with it (`JobContext.discard`). If the worker process is killed outright, a folder under
`video_frames/<asset>/<run>/` with no matching `pipeline_run` can remain; it is safe to delete and is identifiable by that.

## 4. Safety and limits

* Explicit start only. Uploading a video queues a preview, never an analysis. Opening a result reads stored data: no job, run,
  finding, frame, review or audit row is created (tested by counting rows before and after repeated reads).
* Existing upload limits apply (`GEOPOC_MAX_VIDEO_BYTES`, 1 GB). The stored original's checksum is verified before a job reads it.
* A job needs free disk space for its frames (a generous estimate against the storage reserve) and refuses early otherwise.
* At most 300 frames per run (configurable), and 3,000,000 frames per video for the timing scan (`GEOPOC_VIDEO_ANALYSIS_MAX_PACKETS`).
  The scan has a time limit (`GEOPOC_VIDEO_PACKET_SCAN_TIMEOUT_S`, 600 s); each frame extraction has the existing media tool timeout.
* Failure states, each readable and with a stable code: `MEDIA_TOOLING_UNAVAILABLE` (no ffmpeg/ffprobe), `VIDEO_TIMING_UNSUPPORTED`,
  `SAMPLING_EXCEEDS_LIMIT` and the other `SAMPLING_*` codes, `FRAME_EXTRACTION_FAILED`, `FRAME_TIMESTAMP_MISMATCH`, `FRAME_TIMEOUT`,
  `INSUFFICIENT_STORAGE`, `STORAGE_ERROR` (missing or altered original), `INFERENCE_FAILED`, `MODEL_UNAVAILABLE`, `GPU_OUT_OF_MEMORY`
  (retried once). One frame that cannot be taken is listed and the rest are kept (`completed_with_warnings`); if none can, the
  job fails and keeps nothing.
* Project isolation: the plan, frames and frame images are under `/projects/{project}/assets/{asset}/video-analysis/...`. A video or
  run of another project is "not found".

## 5. Does the database schema need to change? No.

Checked, with the reasons:

* `observation.pipeline` already allows `video`; the observation contract already carries `frame_index`, `timestamp_ms`, a
  pixel region and an evidence key, and it already requires a frame or timestamp for a video finding.
* `pipeline_run` is free text for the pipeline and has JSON `settings` and `summary`, so the policy and the counts need no columns.
* `artifact` has `kind`/`variant` and JSON `details`, and no check on the kind; `UNIQUE (asset_id, kind, variant)` holds because the
  variant includes the run id and the frame number.
* The existing job kind `analyze` covers it; its dedupe on `payload` makes the same video + model + sampling a 409 while active.

Trade-offs accepted (not defects, but worth knowing): a frame's link to its run is in `artifact.details` (JSON), not a foreign key,
so the database does not enforce it (the service does, and tests cover it); finding-to-frame lookups use a JSON path on the
finding's evidence. At the intended scale (at most a few hundred frames and a few thousand findings per run) this needs no index.
If runs ever grow far beyond that, a `video_frame` table with foreign keys to the run and to each finding would be the next
design; it would be a migration and would need approval. **None is proposed or written now.**

Two small additive API changes: `GET /projects/{id}/observations` takes an optional `frame_index`, and the CSV export gains two
trailing columns (`frame_index`, `video_timestamp_ms`, empty for photographs) so that a video finding's pixel columns are never
ambiguous. The findings report already carried `frame_index` and `timestamp_ms` and needed no change.

## 6. Interface

On the existing Video Analysis screen: the player, a timeline with one mark per sampled frame (filled when it has findings; click
selects the frame and moves the player), the "Analyse frames" form (model, interval, maximum frames, optional range, the count it
would take, an explicit button), the job with progress and readable failures, a strip of the sampled frames with their timestamps, the
selected frame with its boxes, the findings grouped under the frame they belong to, the existing review panel for the selected
finding, and "How this was produced" (model, sampling, failed frames, checksums, tool version, the policy text). A warning above
the findings states the four limits in section 1. From the Observations screen, a video finding names its frame and links back.

## 7. Tested

* Backend (scratch database `geopoc_test`, real ffmpeg clips, the stub detector): 31 sampling/timestamp tests (pure) and 46
  end-to-end tests (explicit start, validation, limits, B-frames, start offset, 29.97 fps, slow video, evidence checksums,
  tampered/missing original, missing ffmpeg, unsupported timing, a frame that fails, all frames failing, a failing model, a
  cancelled job and a stop after results were written, progress, no whole-video read, no network other than the local database,
  isolation, review, report and CSV, reopening without any write, no linking across frames).
* Frontend: 36 tests on the video screen and helpers, plus the Observations screen and the job panel changes.
* Browser (real Edge, scratch database `geopoc_e2e`, real worker, stub detector, a real ffmpeg clip): explicit start, plan and refusal,
  the job, timeline click moving the real player, boxes on the real frame, review, reload without a second job, the Observations link.

**Not tested:** the real Faster R-CNN on video frames (the weights are not exercised by the automated tests; the stub detector
stands in, and the real adapter is the same one the photograph pipeline uses); very long or very large (4K, many-GB) videos; videos
with several video streams (only the first is used and numbered); variable-rotation or unusual containers beyond MP4, MOV and MKV.
