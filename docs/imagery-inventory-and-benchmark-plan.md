# Representative imagery inventory and reproducible benchmark plan

Status: **plan** (owner decision K-05). Prepared 2026-10-21. No dataset has been downloaded, and no accuracy or numerical acceptance threshold is set here.

## 1. What imagery exists today

The inventory was taken from the working database (read-only queries) and the review folder.

| Where | Item | Kind | Size / grid | Georeferencing | Origin |
|---|---|---|---|---|---|
| D:\geopoc-cache\review | `harbour_scene_utm43n.tif` | 3-band uint8 GeoTIFF | 900 × 600, 10 m | EPSG:32643, valid | **Synthetic**, made for UI review |
| | `scan_without_crs.tif` | 3-band uint8 GeoTIFF | 900 × 600 | None | Synthetic |
| | `complex_values.tif` | 1-band complex64 | 900 × 600 | EPSG:32643 | Synthetic (an unsupported-type test) |
| | `astronaut.png`, `chelsea.png`, `coffee.png` | Ground photos | Small | — | scikit-image sample images |
| | `walkthrough.mp4`, `walkthrough_h264.mp4` | Video | Short | — | Synthetic |
| Test suites | `tests.test_raster_tools.tif()/gradient()` and the Phase 2–4 fixtures | Generated in memory | Any | Controlled | Synthetic |
| Working DB | 3 overhead rasters, 5 ground images, 1 video | The review files above, uploaded | | | Same as above |

**Finding: there is no real-world overhead imagery on this machine.** Every Phase 3b result so far has been shown on synthetic data. Real imagery must come from the owner, or from a public source approved under K-04.

## 2. Categories the evaluation must cover

Each category lists the property that matters and why. The owner chooses what applies; the column "Candidate sources" lists options for approval only, **nothing is downloaded**.

| ID | Category | What it exercises | Candidate sources (for approval) |
|---|---|---|---|
| IM-1 | VHR RGB, 8-bit, georeferenced (0.3–1 m), urban | Display, measurement, SAM 2.1 outlining of buildings and roads | Owner-supplied; or public orthophotos with a clear licence (e.g. national open-data portals; licence per source) |
| IM-2 | VHR RGB, rural / natural | Outlining with low texture contrast | As IM-1 |
| IM-3 | Multispectral 16-bit (Sentinel-2 L2A, 10–20 m) | Band selection, stretch, statistics, comparison over time | Copernicus Sentinel-2 (free, full and open data policy, attribution) |
| IM-4 | Landsat 8/9 Collection 2, 30 m | Large rasters, scale/offset declared in metadata | USGS (public domain) |
| IM-5 | Large raster without overviews (> 10k px on a side) | Display copy build time and tile latency | IM-1/IM-3 originals, or synthetic (§4) |
| IM-6 | Unreferenced scans and sidecar-only georeferencing (`.tfw`, `.prj`) | Unplaced handling, georeferencing workflow, future sidecars | Owner-supplied, or synthetic |
| IM-7 | Projected CRS far from the central meridian, and high-latitude scenes | Planar-versus-geodesic measurement differences | Public or synthetic |
| IM-8 | Same-grid pairs from two dates | Pixel comparison | Sentinel-2 tiles from one MGRS tile and two dates (IM-3) |
| IM-9 | Video, aerial or ground, with known short events | Frame-sampling coverage (K-06), when that is approved | Owner-supplied |

For every item kept, the inventory records:

- source and licence, with a link;
- the original's SHA-256;
- the format, size, bands, data types, CRS and resolution;
- acquisition time, if declared;
- the reason it is included.

**No personal or sensitive imagery is used without the owner saying so.**

## 3. Labelled data

Labels are needed only for AI evaluation (SAM 2.1 first).

- **Reference outlines** are drawn by a person in the application, using the Phase 4 observation tools on the approved imagery. Each records the person, the time, the display settings and the tool.
- Review is two-person where possible: one draws, one reviews. Disagreements are recorded rather than averaged away.
- **Categories:** none for SAM 2.1, which is class-agnostic. For any later detector or classifier, categories and labelling rules are agreed with the owner first (K-03).
- **Public labelled datasets** (for example SpaceNet, DOTA, EuroSAT, BigEarthNet): only after the exact dataset, intended use, licence and size are approved (K-04). DOTA is noted in phase-5-readiness.md as academic-only.

## 4. Reproducible benchmarks

### 4.1 Non-AI performance (available now)

`services/api/tests/bench_display_copy.py` writes a synthetic 12000 × 12000 × 3 uint8 striped GeoTIFF with no overviews. It builds and verifies the display copy, then times tiles at z12, z14 and z17, best of 3, from the original and from the copy. Results on 2026-10-21 are in phase-3b-report.md.

To make the benchmarks reproducible:

- record the machine (CPU, RAM, disk, GPU and driver) with every run, plus package versions and the git state;
- run each case three times and report the best and the median, cold and warm cache noted separately;
- synthetic inputs are generated from fixed code, so they are identical across runs;
- real inputs are identified by SHA-256;
- output goes to JSON under D:\geopoc-cache\bench, which is never committed.

Planned additions:

- statistics job time per megapixel, exact and sampled;
- comparison throughput;
- display copy for IM-3/IM-4/IM-5 once real files are approved.

### 4.2 AI evaluation (after approval)

The protocol is SAM 2.1 first, per sam2.1-approval-brief.md §8.

| Step | Measure | Reported as |
|---|---|---|
| Smoke | Loads offline; no network activity; worker probe passes | pass / fail (mandatory gate) |
| Resources | Peak VRAM; image-encoder time per 1024 px window; latency per prompt | Numbers per checkpoint, with the machine recorded |
| Repeatability | The same image, prompts and settings, run twice | Mask IoU between the runs, and whether bitwise identical |
| Indicative quality | IoU and boundary F-score against reviewed reference outlines; number of prompts needed | Per image and per category; distributions, **not** pass/fail |
| Sensitivity | The effect of the display stretch and band choice on the mask | IoU between settings |

**No acceptance thresholds** are proposed until these indicative runs and the labelled-data review exist (K-05). Thresholds, if any, are then proposed to the owner with the data behind them.

### 4.3 Mandatory gates (pass/fail, every run)

These apply to every AI and non-AI change:

1. **Geospatial correctness:** known-answer tests (synthetic grids with exact expected coordinates, distances and areas) pass, and placement is unchanged.
2. **Provenance:** every result records its inputs (SHA-256), tool or model version, checkpoint hash, settings and person.
3. **Repeatability:** the same inputs give the same outputs, or the difference is measured and reported.
4. **Network isolation:** no outbound connection during analysis (the worker check, plus the browser's external-request log in walkthroughs).
5. **Original-file integrity:** the SHA-256 of every original is unchanged before and after, and no sidecar or PAM file is written next to it.

## 5. What is needed from the owner

1. Which categories in §2 matter for the intended use, and whether owner-supplied imagery exists for them.
2. Approval (dataset, use, licence, size) for any public imagery, for example one Sentinel-2 L2A tile on two dates for IM-3 and IM-8: about 1 GB per date for the full product, less for the 10 m bands only. The size is to be confirmed per product before approval.
3. Who draws and who reviews the reference outlines.
