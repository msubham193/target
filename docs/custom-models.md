# Uploaded ("custom") detection models

Users can add their own object-detection model from **Settings → Models → Add your own model**. Once accepted, it is
offered under **Model** in Photo Analysis and Video Analysis exactly like a model from `models/manifest.yaml`, and
every run records its name, version and file checksum.

## Accepted files

| Format | Runtime | Opening it can run code? | Class names |
|---|---|---|---|
| `.onnx`, a YOLOv5 / v8 / v11 export (detection or segmentation, boxes only) and the end-to-end exports of YOLOv10 / YOLO26 | ONNX Runtime GPU (`onnxruntime-gpu`, MIT) | No | From the export metadata (`names`), or typed by the uploader |
| `.pt`, an Ultralytics YOLO checkpoint (detect, segment, obb, pose; boxes are used) | Ultralytics (`ultralytics`, **AGPL-3.0**) | **Yes**: a pickle. Accepted only with `trust_code=true` | From the file |
| `.pth` (or `.pt` with format `torchvision`), torchvision Faster R-CNN weights: a bare `state_dict` or a checkpoint with `model` / `state_dict` / `model_state_dict` | PyTorch / torchvision | No: read with `weights_only=True`; a whole pickled model is refused | Typed by the uploader (without background), except a 91-class COCO model |

For torchvision the architecture is found by trying each supported one: ResNet-50 FPN (v1, v2) and MobileNet v3 Large
FPN (full and 320). The two MobileNet variants have identical weights, so the full one is chosen unless the uploader
picks the 320 variant.

ONNX decoding (`geopoc/pipelines/adapters/onnx_detection.py`): letterbox to the model's input size (fixed in the
graph, else the export's `imgsz`, else 640), layout from the first output's shape (`[1,4+nc,N]` v8, `[1,N,5+nc]` v5,
`[1,N,6]` end-to-end), per-class NMS (IoU 0.45) except for end-to-end, boxes mapped back to the photograph. The CUDA
provider is used when available and falls back to the CPU if the GPU cannot run the graph; the run records which.
`onnxruntime-gpu` is pinned to 1.24.4 because later releases are built for CUDA 13 while PyTorch here is CUDA 12.8.

Ultralytics is kept offline: `YOLO_OFFLINE=1`, settings in `<DATA_ROOT>/models/ultralytics-config`, usage reporting
(`sync`) switched off.

## Upload flow

`POST /api/v1/models/custom?filename=&name=&version=&format=&classes=&min_score=&architecture=&licence=&trust_code=`,
body: the raw file (not multipart), at most `GEOPOC_CUSTOM_MODEL_MAX_BYTES` (2 GiB).

1. Name (`^[a-z0-9][a-z0-9_.-]{1,62}$`), version, extension and class names are checked; a `.pt` without `trust_code`
   is refused (`TRUST_REQUIRED`).
2. The body is streamed into `<DATA_ROOT>/models/custom/<id>/<filename>` and checksummed.
3. `geopoc.registry.model_check` runs in a **separate process** (the API never imports PyTorch, ONNX Runtime or
   Ultralytics, and never opens the file itself). It loads the file with the adapter that will run it, runs it once on a
   blank image, and returns the class names and resolved settings. Timeout: `GEOPOC_CUSTOM_MODEL_CHECK_TIMEOUT_S`
   (300 s).
4. On success the model is appended to `<DATA_ROOT>/models/custom/registry.yaml` and the upload is audited
   (`model.custom_added`). On any failure the folder is deleted and nothing is recorded.

`DELETE /api/v1/models/custom/{name}/{version}` removes the entry and the file (audited as `model.custom_removed`). It
is refused while a queued or running analysis job uses the model. Past runs and findings keep the model's name,
version and checksum. Models from the manifest cannot be removed this way.

Nothing is stored in the database, so no migration is involved. A job re-verifies the file's size and SHA-256 before
loading it, as for manifest models.

## Honesty rules

An uploaded model is treated like any other: its score is the model's own and **not calibrated**, nothing about it has
been evaluated by this application, and every finding starts as `needs_review`. Its category ids are namespaced
`custom_<name>:<class>`.

## Tests

`services/api/tests/test_custom_models.py` covers the registry, the upload refusals, removal, auditing, ONNX decoding
(v5, v8, segmentation, end-to-end, NMS), torchvision checkpoint handling and class mapping, and one real end-to-end
analysis with a 683-byte ONNX model built for the tests (`tests/fixtures/fake-yolo-3class.onnx`). The web form is
covered by `apps/web/src/components/customModelUpload.test.tsx`.
