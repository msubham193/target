# Model card — fasterrcnn_resnet50_fpn_v2 @ coco_v1

Used by: the ground-image pipeline, for general-purpose object detection in ground photographs.

**Status: integrated and working on this machine. Not evaluated. No accuracy is claimed for this project's imagery.**

**Approved use (2026-10-12, decision log G-03): local PoC evaluation only.** The exact licensing review of the model weights is still pending (U-018) and must be completed before any other use. No accuracy figure and no validated confidence threshold may be claimed until labelled evaluation data exists (G-04).

## What it is

| | |
|---|---|
| Architecture | Faster R-CNN, ResNet-50 backbone with feature pyramid network, "v2" training recipe |
| Source | torchvision (`torchvision.models.detection.fasterrcnn_resnet50_fpn_v2`), weights `COCO_V1` |
| Weights file | `fasterrcnn_resnet50_fpn_v2_coco-dd69338a.pth`, 175,221,657 bytes (167.1 MB) |
| SHA-256 | `dd69338a24b8d7381807e247652bdc356325bcbaf1cd3e092e00e0a1a58706bf` |
| Download | `https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_v2_coco-dd69338a.pth` |
| Parameters | 43.7 million |
| Runs | Locally, on the GPU when available, otherwise on the CPU. Nothing is sent anywhere. |
| Licence | torchvision code is BSD-3-Clause. The weights were trained on COCO 2017; COCO's annotations are CC BY 4.0 and its images carry their own Flickr licences. Confirm the terms suit the project's intended use before relying on it beyond this PoC. |

## What it outputs

Bounding boxes with one of 80 everyday object classes from COCO (people, vehicles, animals, furniture, household items and similar), each with a score.

- **Score meaning:** the classification score for the predicted class after non-maximum suppression. It ranks the model's own detections. It is **not a calibrated probability**.
- **Category ids** stored on observations are `coco:<class name>`, for example `coco:person`.

## Why this one

- General-purpose, well-known and permissively licensed code, with no dependency beyond PyTorch and torchvision.
- Small enough for an 8 GB GPU with a lot of room to spare (see measurements).
- A lighter alternative from the same family (`fasterrcnn_mobilenet_v3_large_fpn`, 74 MB) is supported by the same adapter and can be added to the manifest without code changes.

It was chosen for a first integration, not because it was shown to be the best model for this project's imagery. That comparison needs representative data.

## Known limits

- **Only 80 classes.** Anything else is either missed or forced into the nearest class. It has no notion of the specific features this project may care about.
- **Trained on everyday consumer photographs.** Behaviour on other imagery (unusual viewpoints, low light, haze, long range, infrared) is unknown.
- **Small, distant, occluded or blurred objects** are detected less reliably.
- **It says what something looks like, not what it is for, who owns it, or where on the Earth it is.** A ground photograph has no georeferencing and none is inferred.
- **False positives occur.** In the one manual run recorded below, a second "person" box with a score of 0.68 was reported at the edge of an image; it was not checked against the picture. This is why every finding starts as `needs_review`.

## Settings (in `models/manifest.yaml`)

| Setting | Value | Meaning |
|---|---|---|
| `min_score` | 0.5 | Detections scoring below this are counted on the run but not stored. **An operating cutoff, not a validated threshold.** |
| `max_detections` | 100 | Upper bound on stored detections per image |
| `max_input_side` | 1333 | Images are reduced to this longest side before inference; boxes are scaled back to the original's pixels |
| `device` | auto | GPU if available, otherwise CPU |

## Measurements on this machine

RTX 5060 (8 GB), driver 595.95, PyTorch 2.11.0+cu128. One manual run on 2026-10-12 with a single 512 × 512 photograph, through the real API and worker:

| | |
|---|---|
| Whole job, queue to result | 5.4 s |
| Model load | 0.62 s |
| Inference (one image, first call, includes warm-up) | 0.74 s |
| GPU memory allocated by PyTorch, peak | 470 MB |
| GPU memory in use system-wide: before / peak / after the job's process exited | 1,503 / 2,328 / 1,499 MB |
| Raw detections / stored (≥ 0.5) / below cutoff | 300 / 2 / 298 |

One image is not a benchmark. Throughput, larger images and CPU-only speed were not measured.

## Published figure (not verified here)

torchvision reports a box mAP of 46.7 on COCO val2017 for these weights. That figure describes COCO, not this project's imagery, and was not reproduced.

## Before relying on it

1. Assemble representative, labelled images.
2. Measure precision and recall per class on them.
3. Derive per-class thresholds and record them as calibrated, with the dataset reference.

Until then its scores stay uncalibrated and every finding needs human review.
