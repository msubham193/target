# Model card — roboflow_yolo_online @ hosted_v1

Used by: the ground-image pipeline and video-frame analysis, for object detection with a YOLO model hosted by Roboflow.

**Status: integrated. Not evaluated. ONLINE: every analysed photograph (or sampled video frame) is uploaded to Roboflow.**

## What it is

| | |
|---|---|
| Service | Roboflow hosted inference, `POST https://detect.roboflow.com/<project>/<version>` |
| Model | Whichever model you name in `GEOPOC_ROBOFLOW_MODEL_ID` (for example a YOLO model from Roboflow Universe, or one you trained). Its classes, accuracy and licence are those of that model, not of this application. |
| Runs | On Roboflow's servers. Nothing is installed here: no PyTorch, no weights. |
| Licence | Roboflow's terms of service plus the chosen model's own licence. Check both before using real project photographs. |

## What leaves this machine

- The image, re-encoded as JPEG (quality 90) and reduced to at most 1280 px on its longest side (`max_input_side`). The original file is never uploaded.
- Your API key and the model id, in the request address.
- For a video analysis, one upload per sampled frame (bounded by the frame limit you choose).

No project name, place, coordinates or file name is sent. Roboflow may log or retain what it receives under its own terms.

## How to switch it on

All three are in `.env` (git-ignored). Without them the model is listed as unavailable and says which one is missing.

```
GEOPOC_ONLINE_ENABLED=true
GEOPOC_ROBOFLOW_API_KEY=<your Roboflow private API key>
GEOPOC_ROBOFLOW_MODEL_ID=<project>/<version>
```

Restart the API and the worker afterwards. The key is never returned by the API, never written to the database and never logged.

## Score and cutoff

- The score is the hosted model's own confidence. It is **not a calibrated probability**, and every finding starts as `needs_review`.
- `min_score` (0.4) is passed to Roboflow as its confidence filter, so the count of detections *under* the cutoff is not known for this model.

## Known limits

- Needs an internet connection and a Roboflow account; it is subject to Roboflow's rate and usage limits.
- Results can change if the hosted model version is changed on Roboflow's side. The model id is recorded on each run.
- Not appropriate for photographs that must not leave this machine. Use `fasterrcnn_resnet50_fpn_v2` for those.
