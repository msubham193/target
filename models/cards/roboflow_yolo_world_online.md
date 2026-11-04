# Model card — roboflow_yolo_world_online @ hosted_v1

Used by: the ground-image pipeline and video-frame analysis, for open-vocabulary object detection with YOLO-World, hosted by Roboflow.

**Status: integrated. Not evaluated. ONLINE: every analysed photograph (or sampled video frame) is uploaded to Roboflow.**

## What it is

| | |
|---|---|
| Service | Roboflow hosted inference, `POST https://serverless.roboflow.com/yolo_world/infer` |
| Model | YOLO-World (`v2-l`, set in the manifest). It finds objects by **name**: it is given a list of words and returns boxes for things that match them. Nothing is trained for the names. |
| Runs | On Roboflow's servers. Nothing is installed here: no PyTorch, no weights. |
| Licence | Roboflow's terms of service plus the YOLO-World model's own licence. Check both before using real project photographs. |

## Choosing what it looks for

The names come from `settings.classes` in `models/manifest.yaml`, or from `GEOPOC_ROBOFLOW_WORLD_CLASSES` in `.env`
(comma-separated, which wins when set). Example: `GEOPOC_ROBOFLOW_WORLD_CLASSES=red truck, solar panel, damaged roof`.
Restart the API and the worker after a change.

- **Keep the list short** (a few dozen at most). With more names the scores get less reliable and similar names get confused.
- **Specific, visual names work best** ("storage tank", "shipping container"), not abstract ones ("important thing").
- Something not on the list is never reported. Something on the list may be reported wrongly, for example a white roof as a "tent".
- The class names are recorded on each run, so a result can always be traced to the list that produced it.

## What leaves this machine

- The image, re-encoded as JPEG (quality 90) and reduced to at most 1280 px on its longest side. The original file is never uploaded.
- Your API key (in a request header) and the list of class names.
- For a video analysis, one upload per sampled frame.

No project name, place, coordinates or file name is sent.

## How to switch it on

`GEOPOC_ONLINE_ENABLED=true` and `GEOPOC_ROBOFLOW_API_KEY=...` in `.env`. No model id is needed. Without them the model is
listed as unavailable and says which is missing.

## Score and cutoff

- The score is YOLO-World's own confidence for a name. It is **not a calibrated probability**, and every finding starts as `needs_review`.
- `min_score` (0.3) is sent to Roboflow as its confidence filter, so the count of detections *under* the cutoff is not known.
  It is an operating cutoff, not a validated threshold. A lower value finds more and includes more wrong boxes.

## Known limits

- Needs an internet connection and a Roboflow account; it is subject to Roboflow's rate and usage limits.
- Open-vocabulary models are weaker than a model trained for one specific job. For the thing you care about most, a model trained on
  your own labelled photographs will beat this.
- Satellite imagery: small objects seen from very high up are often missed.
- Not appropriate for photographs that must not leave this machine.
