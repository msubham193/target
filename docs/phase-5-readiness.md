# Phase 5 readiness — raster analysis model selection, evaluation plan and video sampling

Date: 2026-10-21. Status: **planning only; awaiting owner decisions.**

- No code, configuration, model, dataset or dependency was changed, downloaded or installed for this report.
- Phase 4 and the georeferencing follow-up are untouched.
- Nothing is committed.

Read with: the [Phase 4 report](phase-4-report.md), Parts F–J of the [decision log](architecture/decision-log.md), and the specification (`Geospatial_PoC_UI_and_AI_Analysis_Rules_v3.docx`).

---

## 1. Audit: what remains, and what is undecided

### 1.1 A naming mismatch to resolve first

In the **approved implementation plan**:

| Plan phase | Scope |
|---|---|
| Phase 3 | Overhead-raster and video *analysis* pipelines |
| Phase 5 | Aggregator (local Qwen), versioned reports, editable settings and the offline verification run |

Phase 3 delivered the ground-image pipeline only, because no model existed for the other two (U-001). The "Phase 5" requested here is therefore **the deferred part of Phase 3**, not the plan's Phase 5.

There are two ways to proceed, and the choice is the owner's (decision **O-1** in §9):

- **(a) Insert it as "Phase 3b"** before the plan's Phase 5. This is recommended: aggregation and reports are more useful once there is more than one pipeline to aggregate.
- **(b) Keep the plan's order** and do raster/video analysis after reports.

### 1.2 Remaining gaps (from the code, not the screens)

| Area | State | Blocked by |
|---|---|---|
| Overhead-raster analysis (masks, features, statistics: spec V2 §4) | Missing. Jobs for it are refused, not simulated. | U-001 (model, classes, validation data) |
| Video analysis (sampled frames, frame analysis, optional associations: V2 §4, VID-001) | Missing | U-001 (model), U-010 (sampling, association) |
| Calibration and thresholds | Missing, by rule (G-04) | U-004, U-005 (labelled data) |
| Band selection and contrast; pixel inspection; measurements | Missing (Satellite screen shows them as planned) | No decision needed; engineering only (§8) |
| Overview/COG derivatives for large rasters | Missing; tiles render from the original on request | No decision needed |
| Aggregator, versioned reports, PDF | Missing (plan Phase 5) | U-002, U-008, D-014 |
| 3D | Missing (plan Phase 6) | U-012 |

### 1.3 Unresolved decisions relevant here

| ID | Question | Current default |
|---|---|---|
| **U-001** (overhead, video) | Model family, supported classes and a labelled validation set for each pipeline | None; no model |
| **U-010** | Video sampling interval; tracking evaluation; whether cross-frame association is in the PoC | "Configurable fixed interval; association off" (not yet specified or approved) |
| U-004 / U-005 | Calibration, thresholds, quality criteria | No thresholds; everything `needs_review` |
| U-015 | Controlled taxonomy | The model's own class list |
| U-018 | Training-data licence terms of the chosen weights beyond a PoC | Open (applies to every candidate below) |
| U-006 | Coordinate tolerance per CRS | PROJ built-ins; no tolerance asserted |

---

## 2. Intended raster analysis use cases

The specification asks the overhead pipeline to "process supported raster tiles/bands and return masks/features/statistics". It limits the application to general observation (V3 §1, OPS-001). The use cases below are framed that way.

In every case:
- the output is an **observation** with evidence, starting as `needs_review` with `UNCALIBRATED_SCORE`;
- there is **no ranking, prioritisation, or engagement-related output**;
- the existing guardrails apply unchanged: a geographic geometry only via `raster_affine_transform` from the raster's *effective* georeferencing, and `CRS_USER_ASSERTED` when that came from a person.

| ID | Use case | Typical input | Output (observation contract) |
|---|---|---|---|
| **UC-R1** | Land cover / land use mapping | Multispectral (Sentinel-2 10 m, HLS 30 m) or very-high-resolution RGB (0.3–1 m) | Class mask stored as a derived artifact. One observation per class region: `region_px` polygon, geometry when georeferenced. `measurements.area` is valid only with a projected CRS or a geodesic method, and `unavailable` otherwise. |
| **UC-R2** | Building footprint extraction | VHR RGB (≤ 0.5 m) | Polygon observations, category `building`. Area as above. |
| **UC-R3** | General object detection | VHR RGB | Oriented or axis-aligned boxes for general categories (vehicles, ships, aircraft, storage tanks, sports fields…). Counts are per run, never merged across runs without the Phase 4 linking rules. **Needs explicit owner scoping (O-3)** because the source material is a targeting presentation. Only descriptive categories would be offered. |
| **UC-R4** | Change between two co-registered rasters of the same place | Two rasters on the same grid or CRS | Per-pixel difference or change mask; "changed area" regions. A non-AI method is available first (§8); an AI method is optional later. |
| **UC-R5** | Assisted delineation (a person clicks, the model outlines) | Any RGB-displayable raster | Class-agnostic mask from a human prompt; the category comes from the person. `claim_type` `human_entered`, with the model recorded as the tool. |

**Out of scope, in all cases:** target identification for engagement, prioritisation, damage or strike assessment for operational decisions, weapon or impact-point outputs (OPS-001, T-12).

The deciding input is **what imagery the owner actually has** (decision O-2). Sensor, ground sample distance, band set and file size decide which models are even applicable. A model trained on 0.5 m RGB does not transfer to 10 m Sentinel-2, and the reverse is also true.

---

## 3. Candidate models and algorithms

### 3.1 Comparison

The licence column reflects what the primary pages say (sources in §10); "verify" means a primary page did not settle it. **Speed and VRAM are not measured here.** The figures are what the projects publish or what follows from parameter counts. Measuring them on this machine is step 1 of the evaluation (§5.6).

| Candidate | Use case | Input it expects | Code licence | Weights / training-data licence | Windows + RTX 5060 (Blackwell) risk | Fit |
|---|---|---|---|---|---|---|
| **Ultralytics YOLO11-OBB** (DOTA-v1 weights) | UC-R3 | VHR RGB, oriented boxes, 15 DOTA classes | **AGPL-3.0**; Enterprise licence for commercial use | Trained on **DOTA: "any commercial use is prohibited"**; academic only. Google Earth terms also apply. | Low: pure PyTorch | Strong published benchmark (~78–81 mAP50 on DOTA-v1 per Ultralytics). **Usable only for local PoC evaluation** unless both licences change. |
| **MMRotate** (Oriented R-CNN etc., DOTA weights) | UC-R3 | VHR RGB | Apache-2.0 | DOTA (academic only); weight licence not stated: verify | **High**: needs compiled `mmcv` ops; prebuilt wheels for torch 2.11 / CUDA 12.8 / sm_120 on Windows unlikely; Smart App Control risk | Not recommended |
| **torchvision detector, fine-tuned by us** | UC-R3, UC-R2 | VHR RGB | BSD-3 | Depends on our training data (§4) | Low | Needs a training effort, which is outside the plan's "models used as published" assumption |
| **Prithvi-EO-2.0** (IBM/NASA, 300M/600M) | UC-R1 (and flood, burn-scar, crop variants) | **HLS, 30 m, 6 bands in reflectance** (Blue, Green, Red, Narrow NIR, SWIR 1, SWIR 2), optionally multi-temporal | Apache-2.0 (TerraTorch) | **Apache-2.0** (stated on the model page). Fine-tuned heads have their own licences: verify per head. | Low/medium: PyTorch; 300M parameters should fit 8 GB for inference on tiles (to measure) | Good *if* the imagery is HLS/Landsat/Sentinel-2. Useless for VHR RGB. Foundation model: needs a task head. |
| **TorchGeo backbones** (SSL4EO-S12, Landsat, NAIP, SATLAS) | UC-R1 (scene/patch classification) | Sentinel-1/2, Landsat, NAIP | MIT | **Per weight:** SSL4EO/Sentinel-2 CC-BY-4.0; Landsat CC0-1.0; SATLAS ODC-BY; some NC (Scale-MAE, aerial Swin). Listed in the TorchGeo docs. | Low | Backbones only. A classification head must be trained on labelled data (for example EuroSAT, MIT). Gives patch labels, not fine masks. |
| **Clay v1** | UC-R1 (embeddings) | Sentinel-1/2, others | Apache-2.0 | **Apache-2.0** for code *and* weights (stated) | Low | Embeddings plus a downstream head; same training need as TorchGeo |
| **SegFormer / U-Net trained on LoveDA or OpenEarthMap** | UC-R1 at VHR | 0.25–0.5 m RGB | Model code permissive | LoveDA **CC BY-NC-SA 4.0, academic only**. OpenEarthMap labels follow per-region source licences, CC BY-NC-SA 4.0 where the imagery is unlicensed. | Low | Only for local evaluation; non-commercial |
| **Model trained on SpaceNet** (buildings) | UC-R2 | 0.3–0.5 m WorldView RGB/MS | varies | SpaceNet data **CC BY-SA 4.0** (commercial allowed; ShareAlike). Whether weights trained on it are a derivative is a legal question. | Low | Plausible permissive path for buildings; needs training or a published model with a stated licence |
| **SAM 2.1** (Meta) | UC-R5 | RGB (display-stretched rasters work) | Apache-2.0 | **Apache-2.0** for checkpoints. Sizes 38.9M (tiny) to 224.4M (large) parameters. | Low: the optional CUDA extension is not required | **Best licence position.** Class-agnostic, so no class-accuracy claim arises; quality is measurable as mask IoU. |
| **DeepForest** | Tree crowns (narrow) | ~10 cm aerial RGB | MIT | NEON-trained release model, listed as MIT (verify) | Low | Only if tree inventory is a use case |
| **Classical change detection** (differencing, ratio, CVA) | UC-R4 | Two co-registered rasters | ours | none | none | Deterministic, explainable; no model. Recommended before any AI change model. |
| **ChangeFormer etc. on LEVIR-CD** | UC-R4 | 0.5 m RGB pairs | MIT (code) | LEVIR-CD **academic only, commercial prohibited** | Low | Evaluation only |

### 3.2 How every candidate would handle CRS and files

None of these models understands CRS: they all work in pixel space. The pipeline, as designed in Phases 3–4, would:

1. read windows from the **unchanged original**, never a modified copy, in tiles with overlap. Models are given band order and scaling explicitly, from asset metadata or a human-recorded band mapping, and the band order is never assumed;
2. run inference per tile and merge across tile edges with a recorded rule;
3. map pixel results to geometry only through the raster's effective affine transform and CRS, with `CRS_USER_ASSERTED` if recorded by a person. Rasters that cannot be placed get pixel-space results only;
4. store masks as derived artifacts with checksums and lineage to the asset, run and job (RAS-001).

### 3.3 Recommendation, pending the owner's imagery and licence decisions

- **Track A — permissive, low risk, can start first:**
  - **SAM 2.1** for assisted delineation (UC-R5);
  - the **non-AI raster tools** in §8.
  
  Neither produces a model-asserted class, so neither raises an accuracy claim that needs labelled data.
- **Track B — classified outputs:** choose by imagery (O-2) and licence posture (O-4):
  - **VHR RGB with general objects:** YOLO11-OBB with DOTA weights, **for local PoC evaluation only** (like G-03 for COCO), with AGPL-3.0 and DOTA's no-commercial-use term recorded under U-018. A permissive production path needs a model trained on permissively licensed data (for example SpaceNet for buildings).
  - **Sentinel-2 / Landsat / HLS:** Prithvi-EO-2.0 (Apache-2.0), with a task head whose licence is verified, *or* a TorchGeo CC0/CC-BY backbone plus a head trained on EuroSAT (MIT) or BigEarthNet (CDLA-Permissive-1.0).
- **Not recommended:** MMRotate and Detectron2-based stacks. Their compiled CUDA extensions are a real risk on Windows with Blackwell and Smart App Control.

---

## 4. Labelled evaluation datasets

The licences below are as stated by the dataset owners where quoted; anything marked "verify" was not confirmed from a primary page. **Public benchmarks give indicative numbers only.** They are not representative of the owner's imagery, so the evaluation that decides acceptance must include an **owner-supplied labelled set** (O-6).

| Dataset | Use case | Imagery | Licence (commercial use?) | Size | Notes |
|---|---|---|---|---|---|
| **DOTA v1/v2** | UC-R3 | Google Earth, GF-2, JL-1, aerial | **Academic only; "any commercial use is prohibited"**; Google Earth terms apply | v1 ≈ 2.8k images; v2 18 classes | The benchmark the YOLO11-OBB weights were trained on: **not independent** for evaluating those weights |
| **xView (2018)** | UC-R3 | WorldView-3, 0.3 m, 60 classes | Registration terms; widely listed as CC BY-NC-SA 4.0 (**verify**) | ≈ 1M boxes, ~31 GB | Independent of DOTA; non-commercial |
| **SpaceNet** (2, 7…) | UC-R2, UC-R4 (SN7 is multi-temporal) | WorldView, 0.3–0.5 m | **CC BY-SA 4.0** (commercial allowed, ShareAlike); verify per release | Large (AWS open data) | Best permissive building and change source |
| **Inria Aerial Image Labeling** | UC-R2 | 0.3 m aerial | Built from public-domain imagery and footprints; terms to **verify** on the official site | About 810 km² over 10 cities, with training labels for 5 (verify) | Test labels withheld |
| **LoveDA** | UC-R1 (VHR) | 0.3 m, 7 classes | **CC BY-NC-SA 4.0, academic only** | 5,987 images | Urban and rural domains |
| **OpenEarthMap** | UC-R1 (VHR) | 0.25–0.5 m, 8 classes, 44 countries | Per-region source licences; labels CC BY-NC-SA 4.0 where the source is unlicensed (**verify per region**) | 5,000 images | Geographically diverse |
| **EuroSAT** | UC-R1 (patch classification, Sentinel-2) | 13 bands, 64×64 px, 10 classes | **MIT**, plus Copernicus terms for the imagery | 27,000 patches | Permissive; scene-level only |
| **BigEarthNet v2 (reBEN)** | UC-R1 (multi-label, S1/S2) | Sentinel-1/2, 19 classes | **CDLA-Permissive-1.0** | Large | Permissive |
| **LEVIR-CD** | UC-R4 | 0.5 m Google Earth pairs | **Academic only; commercial prohibited** | 637 pairs | Change benchmark |
| **Owner-supplied set** | All selected use cases | The owner's real imagery | Owner's | Proposed: ≥ 200 labelled tiles or scenes per use case, ≥ 50 instances per class (to be agreed) | **Required for acceptance** (§5) |

**Licence-use question for the owner (O-4):** many of these sets permit only non-commercial or academic use. Whether internal PoC evaluation counts as "non-commercial" is a legal judgement, not an engineering one.

Downloads: none has been downloaded. Each would be fetched only on approval, into `D:\geopoc-data\eval\` (not Git), with its checksum and licence recorded.

---

## 5. Evaluation protocol (proposed; acceptance numbers to be set by the owner)

### 5.1 Principles

1. No accuracy figure is published from this protocol until it has been run on a frozen test set (G-04).
2. Thresholds are chosen on a *calibration* split and reported on a separate *test* split, never tuned on test.
3. Every number carries its dataset version, model checksum, settings and a confidence interval.
4. The same harness, code version and settings must reproduce the same predictions. Any GPU non-determinism tolerance is documented.

### 5.2 Data handling

- An **evaluation manifest** (YAML, in Git) lists each file with its SHA-256, source, licence, split (`calibration` / `test`), sensor, GSD, bands, CRS status and scene stratum. The data itself stays outside Git.
- **Stratification:**
  - urban, rural, coastal and other scene types;
  - season and illumination;
  - off-nadir angle where known;
  - GSD bins;
  - georeferenced vs not.
- **Required failure-case fixtures,** each with an expected *behaviour* rather than an expected accuracy:

| Fixture | Expected behaviour |
|---|---|
| Cloud or haze cover; heavy shadow | Flagged or degraded, never silently confident |
| Nodata borders; partial tiles at edges | No detections inside nodata |
| GSD outside the model's training range | `LOW_RESOLUTION` or "outside the model's supported input" |
| Band order or scaling mismatch (for example 16-bit reflectance given to an 8-bit RGB model) | Refused, or converted with the conversion recorded |
| Missing CRS; recorded (user-asserted) CRS; rotated geotransform | Pixel results only; `CRS_USER_ASSERTED`; correct rotated geometry respectively |
| Very large raster (near the 1 GB limit) | Completes within the resource limits, or fails with a recorded code |
| Duplicate file | Phase 4 duplicate links, not double counting |
| Empty scene | Zero findings is a valid result (T-10) |

### 5.3 Metrics per use case

| Use case | Primary metrics | Also report |
|---|---|---|
| UC-R3 detection | Per-class precision and recall at IoU 0.5 (oriented IoU for OBB); AP50 and AP50:95 | PR curves; false positives per km²; confusion between classes; performance by stratum |
| UC-R2 buildings | Instance F1 at IoU ≥ 0.5 (SpaceNet-style matching) | Area error (relative), only on georeferenced tiles with a projected CRS |
| UC-R1 land cover | Per-class IoU, mIoU, per-class F1, overall accuracy | Confusion matrix; boundary F1; area per class vs reference, with its method |
| UC-R4 change | F1 of the change class, false-alarm rate | Per-stratum results; sensitivity to misregistration (shift tests of 1–5 px) |
| UC-R5 assisted delineation | Mask IoU vs reference polygons after 1, 3 and 5 clicks | Time per object (with a person) |
| Scores (all) | Reliability diagram and expected calibration error per class on the calibration split | Thresholds selected there, frozen for test |
| Geometry (all) | Positional error in metres of output geometries against reference vectors with known accuracy | Only where the reference data's own accuracy is stated (U-006) |

Confidence intervals: bootstrap over images or tiles (≥ 1,000 resamples), 95%.

### 5.4 Acceptance criteria

**Hard gates** (non-negotiable; proposed, need owner sign-off):
- 100% of outputs carry complete provenance: asset, run, model checksum, settings, evidence region.
- 0 geographic geometries from rasters without valid effective georeferencing.
- 0 network connections during evaluation; models load from local files with checksums.
- Peak VRAM below 7.0 GB on the RTX 5060 (8 GB) at the chosen tile size.
- Predictions are reproducible with the same settings.
- Originals are byte-identical before and after.

**Quality gates** (numbers **to be set by the owner**, per class and use case; none is proposed here, to avoid implying a validated figure):
- for detection: minimum precision at a minimum recall;
- for land cover: minimum per-class IoU;
- maximum false alarms per km² for change detection.

Until these are set and met, every output stays `needs_review` and labelled uncalibrated. That is the current behaviour, so the application is not blocked; it is only not allowed to claim quality.

### 5.5 What the evaluation run produces

- An evaluation report artifact (JSON and Markdown) per model version, covering metrics with intervals, per-stratum tables, failure-case results, calibration plots and resource measurements.
- It is stored outside Git and indexed in the decision log.
- A model can move from "PoC evaluation" to "approved for use" only through an owner decision that cites that report.

### 5.6 Performance measurement (first step after model approval)

Measure on this machine, and record in the model card:
- load time;
- latency per 1024 × 1024 tile;
- peak VRAM and RAM;
- throughput in km² per minute at native GSD;
- end-to-end time for the largest permitted raster;
- CPU-only fallback time.

No performance claim is made before this measurement.

---

## 6. Video: frame-sampling proposal (for U-010)

### 6.1 Options

| Option | Temporal coverage | Cost | Timestamps and reproducibility | Comment |
|---|---|---|---|---|
| **A. Fixed time interval by presentation timestamp (PTS)** (for example 1 frame/s) | Even in time, whatever the frame rate; events shorter than the interval can be missed | Proportional to duration, not to frame rate | Exact per frame (PTS from the stream); reproducible with fixed settings | **Recommended default** |
| B. Every Nth decoded frame | Even in frames; uneven in time for variable-frame-rate video | Proportional to frame count | Frame index exact; time depends on VFR | Simpler, but misleading for VFR footage |
| C. Keyframes (I-frames) only | Depends on the encoder's GOP (0.5–10 s typical) | Cheapest (no full decode) | Exact, but the interval is encoder-chosen and uneven | Not controllable; not recommended as the default |
| D. Scene-change sampling | Adapts to content | Medium | Reproducible only with a fixed threshold and decoder; the threshold is a tuning parameter | Useful later as an option, not as the default |
| E. Every frame | Complete | Highest (25–60× option A at 1 frame/s) | Exact | Only for short clips or after approval |

### 6.2 Recommendation (needs approval)

**Default policy: option A.**
- **Rate:** one frame per second of presentation time, configurable from 0.2 to 5 frames/s per job. The first decodable frame and the last frame are always included.
- **Selection rule:** for each target time *t = k / rate*, take the first decoded frame whose PTS is ≥ *t*. This is deterministic and correct for variable frame rates. A frame is never selected twice.
- **Recorded per sampled frame:**
  - frame index (decode-order count from the start);
  - PTS in stream `time_base` units, and `timestamp_ms` derived from it;
  - the target time;
  - the SHA-256 of the decoded RGB pixels;
  - extraction settings: ffmpeg version, filter graph, sampling rate and the selection rule.
  
  These go into the pipeline run and each observation's evidence (`frame_index`, `timestamp_ms`), per VID-001.
- **Evidence frames:** frames that carry findings are stored losslessly (PNG) as derived artifacts linked to the asset, run and job. Other sampled frames are not stored; they can be re-extracted, and a checksum proves the re-extraction matches.
- **Coverage disclosure:** the run records the fraction of the duration sampled and the longest gap. The UI must say "N of M frames analysed (1 per second); events shorter than 1 s may be missed". A zero-findings result means "nothing found in the sampled frames", never "nothing in the video".
- **Timestamps:** these are positions in the video, relative to the start of the stream. A wall-clock time is shown only if the container states a creation time, and is then flagged as file metadata, not verified (as today).
- **Reproducibility test (VID-001):** re-extract with the same settings. Frame indices must be identical, timestamps identical to the millisecond, and decoded-pixel checksums identical with the same ffmpeg build.
- **Performance:** at 1 frame/s, one minute of video gives 60 inferences. Total time = duration × rate × per-frame latency, measured per model (§5.6). With the current ground detector, this is to be measured before any figure is quoted.

### 6.3 Frame analysis model and association

- **Frame model (U-001, video):** the simplest honest option is to reuse the approved ground-image detector on each sampled frame. Its scope and licence are already decided for PoC evaluation (G-03).
- **Cross-frame association:** recommended **off** for the PoC. If enabled later, it would be a simple IoU-based linker, labelled *derived and uncertain* (R-016). It would create Phase 4 possible-duplicate links, never merged observations. It would need labelled video and tracking metrics (IDF1 / HOTA) before any identity claim is made.
- **No location from video** unless camera pose, calibration or geospatial metadata supports it (V3 §6.3). That is unchanged.

---

## 7. What stays unchanged

These guarantees carry into any analysis work:
- originals are never written, and their checksums are re-verified before inference;
- the Phase 4 review, revision, conflict and duplicate rules;
- georeferencing from the effective version only, with `user_asserted` labelling;
- local-only processing, with models loaded from local files with checksums (`geopoc.devtools fetch-model`, run by hand);
- the stub models for tests;
- OPS-001.

---

## 8. Non-AI raster capabilities that can be built independently

None of these needs a model or labelled data. Each would be labelled as what it is:
- `claim_type: metadata_derived`, or a measurement with its method;
- never presented as AI analysis;
- never given a model score.

| # | Capability | Value | Guardrails |
|---|---|---|---|
| N-1 | **Band selection and contrast stretch** for display (already planned on the Satellite screen) | Inspect multispectral data | Display only; the stretch is recorded; measured values are never changed |
| N-2 | **Pixel value inspection** at a clicked point (all bands, nodata, data type) | Ground truthing; QA | Raw stored values with units only if the metadata gives them |
| N-3 | **Per-band statistics and histograms** (whole raster or a drawn region) | QA; cloud/nodata checks | States the sample (full or decimated) |
| N-4 | **Spectral indices** (NDVI, NDWI…) | Vegetation/water screening | **Only when band identity is known** from metadata or a human-recorded band mapping; band order is never assumed; output marked "index value, not a classification" |
| N-5 | **Distance and area measurement** on georeferenced rasters | Measurement | Geodesic or projected method recorded; `unavailable` without valid georeferencing; `CRS_USER_ASSERTED` carried |
| N-6 | **Overview/COG derivative** per large raster, verified against the original (RAS-001) | Fast tiles for large files (Phase 4 limitation) | Derived asset with lineage; the original is still the source of truth |
| N-7 | **Clip / reproject export** with provenance | Hand-off to GIS tools | New derived asset; transformation and resampling recorded |
| N-8 | **Footprint overlap and coverage** between rasters and the AOI | Which rasters cover what | Only for placed rasters |
| N-9 | **Pixel differencing** of two co-registered rasters on the same grid | Change screening (UC-R4, non-AI) | Labelled "pixel difference, not an interpretation of change"; refused if the grids differ or registration is unknown |
| N-10 | **Sidecar reading** (`.prj`, `.tfw`, `.aux.xml`) to *prefill* a georeferencing proposal | Fewer typing errors | Still reviewed and confirmed by a person (P4-15 to P4-20) |

The recommended first batch is N-1, N-2, N-3, N-5 and N-6: no decisions are needed, and each removes a documented Phase 4 limitation.

---

## 9. Owner decisions needed to proceed

Each item below is a separate decision. ★ marks those that block any inference work.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **O-1** | Where raster/video analysis sits in the plan | (a) Insert "Phase 3b" before the plan's Phase 5; (b) keep the plan's order | (a) |
| **O-2** ★ | Which imagery the analysis must handle | Sensor(s); GSD range; band sets; bit depth; typical and maximum size; georeferenced or not | Provide 3–5 representative sample files |
| **O-3** ★ | Which use cases are in scope | UC-R1 to UC-R5 (any combination); for UC-R3, the list of permitted general categories | UC-R5 + non-AI tools first; UC-R1 or UC-R2 next; UC-R3 only with an explicit category list |
| **O-4** ★ | Licence posture | (a) Local PoC evaluation only: AGPL code and academic-only training data acceptable, as under G-03; (b) intended commercial or operational use: permissive code *and* weights *and* training data only | State (a) or (b); legal review of non-commercial dataset terms for evaluation |
| **O-5** ★ | Model per approved use case | From §3.3 | SAM 2.1 (UC-R5); Prithvi-EO-2.0 or TorchGeo + EuroSAT/BigEarthNet head (UC-R1, multispectral); YOLO11-OBB for PoC evaluation only (UC-R3, VHR) |
| **O-6** ★ | Evaluation data | Owner-labelled set (size, classes, who labels); which public sets to download | Owner set required for acceptance; public sets for indicative results only |
| **O-7** ★ | Acceptance thresholds | Per class and use case (§5.4 quality gates) | Set by the owner after the first indicative run; hard gates as proposed |
| **O-8** ★ | Permission to download and install | Specific weights (SAM 2.1 tiny–large; Prithvi 300M; YOLO11-OBB), packages (`sam2`, `ultralytics` (AGPL), `terratorch`), datasets | Approve per item, with sizes listed before download |
| **O-9** ★ (video) | Sampling policy (U-010) | §6.1 options A–E; default rate | Option A, 1 frame/s, configurable 0.2–5 frames/s |
| **O-10** ★ (video) | Video frame model and association | Reuse the ground detector; other model; association on or off | Reuse the ground detector; association off |
| **O-11** | Non-AI capabilities to build first | §8 | N-1, N-2, N-3, N-5, N-6 |
| **O-12** | U-018 for any weights chosen | Close per model before use beyond PoC evaluation | Record per model card |

**What can start without waiting:** the non-AI batch (O-11), if approved on its own. It needs no model or data.

**What must wait:**
- all inference work: until O-2 to O-8 are decided;
- video inference: until O-9 and O-10 are decided.

---

## 10. Sources consulted (2026-10-21)

| Topic | Source |
|---|---|
| DOTA terms and categories | https://captain-whu.github.io/DOTA/dataset.html |
| Prithvi-EO-2.0 licence and inputs | https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-300M |
| EuroSAT licence | https://github.com/phelber/EuroSAT |
| LoveDA licence | https://github.com/Junjue-Wang/LoveDA |
| SAM 2 licence and sizes | https://github.com/facebookresearch/sam2 |
| TorchGeo code | https://github.com/microsoft/torchgeo |
| TorchGeo per-weight licences | https://docs.torchgeo.org/en/latest/api/models.html |
| xView site | https://xviewdataset.org/ (terms not shown; secondary listing at https://datasetninja.com/xview) |
| OpenEarthMap licence | https://zenodo.org/record/7223446 and https://openaccess.thecvf.com/content/WACV2023/papers/Xia_OpenEarthMap_A_Benchmark_Dataset_for_Global_High-Resolution_Land_Cover_Mapping_WACV_2023_paper.pdf |
| BigEarthNet licence | https://bigearth.net/ |
| LEVIR-CD terms | https://justchenhao.github.io/LEVIR/ |
| Clay licence | https://github.com/Clay-foundation/model |
| SpaceNet licence (secondary) | https://github.com/radiantearth/data-registry/blob/master/datasets/spacenet_2.yaml, https://www.iqt.org/?p=28456 |
| Ultralytics licensing | https://huggingface.co/Ultralytics/YOLO11, https://pypi.org/project/ultralytics/ |
| MMRotate | https://github.com/open-mmlab/mmrotate, https://pypi.org/project/mmrotate |
| Inria (secondary) | https://docs.rcac.purdue.edu/datasets/geoai/AerialImageDataset |
| DeepForest (secondary) | https://pypi.python.org/pypi/DeepForest |

Where only a secondary source was found, the tables above say "verify". Licence terms should be re-checked on the day a download is approved, because they change.
