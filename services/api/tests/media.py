"""Small synthetic media generated in memory for tests. No fixture files are stored in the repository."""

import io
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.io import MemoryFile
from rasterio.transform import Affine, from_origin


def _picture(size=(64, 48), seed=1) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8), "RGB")


def image_bytes(fmt: str, size=(64, 48), seed=1, exif: Image.Exif | None = None) -> bytes:
    buffer = io.BytesIO()
    options = {"exif": exif} if exif is not None else {}
    _picture(size, seed).save(buffer, fmt, **options)
    return buffer.getvalue()


def exif_block(*, original: str | None = None, offset: str | None = None, gps: bool = False) -> Image.Exif:
    exif = Image.Exif()
    exif[271] = "TestCam"
    detail = {}
    if original:
        detail[36867] = original
    if offset:
        detail[36881] = offset
    if detail:
        exif[0x8769] = detail
    if gps:
        exif[0x8825] = {1: "N", 2: (28.0, 36.0, 0.0), 3: "E", 4: (77.0, 12.0, 0.0)}
    return exif


NORTH_UP = from_origin(500000, 3000000, 10, 10)  # EPSG:32643, on the 75 E central meridian


def geotiff_bytes(crs="EPSG:32643", transform: Affine | None = NORTH_UP, size=(40, 30), bands=1) -> bytes:
    profile = {"driver": "GTiff", "width": size[0], "height": size[1], "count": bands, "dtype": "uint8"}
    if crs is not None:
        profile["crs"] = crs
    if transform is not None:
        profile["transform"] = transform
    data = np.arange(size[0] * size[1] * bands, dtype=np.uint32).reshape(bands, size[1], size[0]) % 251
    with MemoryFile() as memory:
        with memory.open(**profile) as dst:
            dst.write(data.astype("uint8"))
        return memory.read()


def make_video(path: Path, seconds=1, rate=10) -> bytes:
    """A tiny test-pattern clip written by the locally installed ffmpeg; the container follows the extension."""
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={seconds}:size=64x48:rate={rate}",
            "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return path.read_bytes()
