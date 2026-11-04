"""Preview images for uploaded assets.

A preview is a small derived picture for looking at an asset in the browser. It is never the original, carries
no georeferencing, and is in the same pixel orientation as the original (EXIF orientation is not applied), so
image-space overlays line up. Originals are only ever opened for reading.

Memory is bounded for every kind of input:
  images   JPEG is decoded at reduced scale; other formats are capped in pixels at ingestion
  rasters  a decimated read straight to preview size; the full raster is never held in memory
  video    one frame, scaled by ffmpeg while decoding
"""

import hashlib
import os
import subprocess
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from sqlalchemy import delete

from geopoc.config import get_settings
from geopoc.db.models import Artifact, Asset, Blob
from geopoc.ingestion.video import _NO_WINDOW, tool_path
from geopoc.jobs.handlers import JobContext, JobError, Outcome, TransientJobError
from geopoc.storage import store

# variant -> longest edge in pixels
VARIANTS: dict[str, int] = {"view": 1280, "thumb": 256}
MEDIA_TYPE = "image/webp"
# Above this many source pixels a raster is sampled (nearest) instead of averaged, to bound the work done.
AVERAGE_LIMIT_PIXELS = 64_000_000
RASTER_CACHE_MB = 64


class PreviewUnsupported(Exception):
    """This asset is valid but cannot be turned into a picture by the current code."""


@dataclass
class Rendered:
    image: Image.Image
    details: dict[str, Any]


def _fit(width: int, height: int, longest: int) -> tuple[int, int]:
    scale = min(1.0, longest / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def render_image(path: Path) -> Rendered:
    with Image.open(path) as image:
        source = image.size
        if image.format == "JPEG":
            image.draft("RGB", (VARIANTS["view"], VARIANTS["view"]))  # decode at 1/2, 1/4 or 1/8 scale
        image.seek(0)
        picture = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        picture.thumbnail((VARIANTS["view"], VARIANTS["view"]))
    return Rendered(picture, {"source_size": list(source), "orientation": "as stored; EXIF orientation not applied"})


def render_raster(path: Path) -> Rendered:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        # Cap GDAL's block cache (its default is a share of system RAM) so a huge raster cannot fill memory.
        with rasterio.Env(GDAL_CACHEMAX=RASTER_CACHE_MB), rasterio.open(path) as src:
            kinds = {np.dtype(dtype).kind for dtype in src.dtypes}
            if "c" in kinds:
                raise PreviewUnsupported("Rasters with complex-valued pixels cannot be shown as a picture.")
            if not kinds <= {"u", "i", "f"}:
                raise PreviewUnsupported("This raster's pixel type cannot be shown as a picture.")
            bands = [1, 2, 3] if src.count >= 3 else [1]
            width, height = _fit(src.width, src.height, VARIANTS["view"])
            resampling = Resampling.average if src.width * src.height <= AVERAGE_LIMIT_PIXELS else Resampling.nearest
            # A decimated read: GDAL streams the source block by block and returns only the small array.
            data = src.read(bands, out_shape=(len(bands), height, width), resampling=resampling, masked=True)
            source = (src.width, src.height)
            dtype = src.dtypes[0]

    stretch: list[dict[str, float] | None] = []
    channels = []
    for band in data:
        values = band.compressed().astype("float64")
        values = values[np.isfinite(values)]
        if dtype == "uint8":
            scaled = band.filled(0).astype("uint8")
            stretch.append(None)
        elif values.size == 0:
            scaled = np.zeros(band.shape, dtype="uint8")
            stretch.append(None)
        else:
            low, high = (float(v) for v in np.percentile(values, [2, 98]))
            if high <= low:
                low, high = float(values.min()), float(values.max())
            span = (high - low) or 1.0
            filled = np.nan_to_num(band.filled(low).astype("float64"), nan=low, posinf=high, neginf=low)
            scaled = (np.clip((filled - low) / span, 0, 1) * 255).astype("uint8")
            stretch.append({"low": low, "high": high})
        channels.append(scaled)

    array = np.dstack(channels) if len(channels) == 3 else channels[0]
    details = {
        "source_size": list(source),
        "bands_shown": bands,
        "resampling": resampling.name,
        # The picture is for looking only. Its brightness is rescaled; it is not the measured values.
        "display_stretch": "none (8-bit values shown as stored)"
        if all(s is None for s in stretch)
        else {
            "method": "linear between the 2nd and 98th percentile of the decimated sample, per band",
            "bands": stretch,
        },
        "georeferencing": "none; the preview is image space only",
    }
    return Rendered(Image.fromarray(array, "RGB" if array.ndim == 3 else "L"), details)


def render_video(path: Path, duration_ms: int | None) -> Rendered:
    ffmpeg = tool_path("ffmpeg")
    if not ffmpeg:
        raise TransientJobError("MEDIA_TOOLING_UNAVAILABLE", "ffmpeg is not installed or not on PATH.")
    # One frame from a tenth of the way in (at most 5 s), so a black first frame is usually avoided.
    seek_s = min((duration_ms or 0) / 10_000, 5.0)
    longest = VARIANTS["view"]
    scale = f"scale='if(gte(iw,ih),min({longest},iw),-2)':'if(gte(iw,ih),-2,min({longest},ih))'"
    handle, frame_path = tempfile.mkstemp(suffix=".png", dir=store.uploads_dir())
    os.close(handle)
    try:
        for seek in dict.fromkeys((seek_s, 0.0)):  # fall back to the first frame if seeking yields nothing
            command = [ffmpeg, "-v", "error", "-y", "-ss", f"{seek:.3f}", "-i", str(path), "-map", "0:v:0"]
            command += ["-frames:v", "1", "-vf", scale, frame_path]
            done = subprocess.run(
                command,
                capture_output=True,
                timeout=get_settings().media_tool_timeout_s,
                creationflags=_NO_WINDOW,
                check=False,
            )
            if done.returncode == 0 and os.path.getsize(frame_path) > 0:
                with Image.open(frame_path) as frame:
                    picture = frame.convert("RGB")
                return Rendered(
                    picture, {"frame_time_requested_s": seek, "note": "one frame; not a summary of the video"}
                )
    except subprocess.TimeoutExpired as exc:
        raise JobError("PREVIEW_FAILED", "The video frame could not be read within the time limit.") from exc
    finally:
        try:
            os.unlink(frame_path)
        except OSError:
            pass
    raise JobError("PREVIEW_FAILED", "No frame could be decoded from the video.")


def _save(picture: Image.Image, relative: str) -> tuple[int, str]:
    """Write a derived picture under DATA_ROOT atomically. Returns its size and checksum."""
    target = store.absolute_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    picture.save(temp, "WEBP", quality=82, method=4)
    data = temp.read_bytes()
    os.replace(temp, target)
    return len(data), hashlib.sha256(data).hexdigest()


def run_preview_job(ctx: JobContext) -> Outcome:
    session, job = ctx.session, ctx.job
    asset = session.get(Asset, job.asset_id) if job.asset_id else None
    if asset is None:
        raise JobError("ASSET_NOT_FOUND", "The asset this job refers to does not exist.")
    blob = session.get(Blob, asset.blob_sha256)
    original = store.absolute_path(blob.storage_path)
    if not original.is_file():
        raise JobError("STORAGE_ERROR", "The stored original is missing.")

    ctx.stage("rendering", status="processing")
    try:
        if asset.modality == "ground_image":
            rendered = render_image(original)
        elif asset.modality == "overhead_raster":
            rendered = render_raster(original)
        else:
            rendered = render_video(original, asset.video.duration_ms if asset.video else None)
    except PreviewUnsupported as exc:
        raise JobError("PREVIEW_UNSUPPORTED", str(exc)) from exc
    except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
        raise JobError("PREVIEW_FAILED", "The original could not be read to make a preview.") from exc

    ctx.stage("saving", progress=0.6)
    artifacts = []
    for variant, longest in VARIANTS.items():
        picture = rendered.image.copy()
        picture.thumbnail((longest, longest))
        relative = f"previews/{asset.id}/{variant}.webp"
        try:
            size, checksum = _save(picture, relative)
        except OSError as exc:
            raise TransientJobError("STORAGE_ERROR", "The preview could not be written to local storage.") from exc
        artifacts.append(
            Artifact(
                asset_id=asset.id,
                kind="preview",
                variant=variant,
                storage_path=relative,
                media_type=MEDIA_TYPE,
                width=picture.width,
                height=picture.height,
                size_bytes=size,
                sha256=checksum,
                details=rendered.details,
                created_by_job_id=job.id,
            )
        )

    ctx.check_cancel()
    # Replace earlier previews of this asset in one transaction. Only derived files are ever replaced.
    session.execute(delete(Artifact).where(Artifact.asset_id == asset.id, Artifact.kind == "preview"))
    session.add_all(artifacts)
    return Outcome(
        result={"variants": {a.variant: [a.width, a.height] for a in artifacts}},
        message="Preview ready.",
    )
