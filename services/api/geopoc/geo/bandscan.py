"""Find locally stored Sentinel-2 L2A bands that can be combined into an index, in one server-configured folder.

The folder is the only place looked at (GEOPOC_IMAGERY_BAND_ROOT). A scene is a product metadata file
(MTD_MSIL2A.xml) and the band files its own image references name. Nothing here is supplied by a client: no path, no
URL and no file name ever comes from a request, and nothing in a result carries a path, only file names. Symbolic links
are ignored, the walk is shallow and bounded, and every file is checked to lie inside the folder after resolution.
Bands are only opened for reading, and only to report what they are.
"""

import hashlib
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import rasterio
from rasterio.errors import NotGeoreferencedWarning

from geopoc.geo import indices
from geopoc.geo.indices import MAX_XML_BYTES, IndexInputError, IndexSpec, ProductInfo

PRODUCT_FILE = "MTD_MSIL2A.xml"
BAND_SUFFIXES = (".jp2", ".tif", ".tiff")
MAX_DEPTH = 3  # how far below the folder a product metadata file may be
MAX_SCENE_DEPTH = 5  # how far below it the bands may be (SAFE: GRANULE/<id>/IMG_DATA/R10m/ is four)
MAX_DIRECTORIES = 2_000
MAX_FILES_PER_SCENE = 20_000
MAX_SCENES = 50


@dataclass
class BandFile:
    role: str  # "red" / "nir"
    band: str  # "B04"
    file_name: str | None = None
    path: Path | None = None  # server side only; never returned to a client
    found: bool = False
    readable: bool = False
    width: int | None = None
    height: int | None = None
    bands: int | None = None
    dtype: str | None = None
    crs: str | None = None
    pixel_size: list[float] | None = None
    scaling: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)


@dataclass
class Scene:
    scene_id: str
    product: ProductInfo | None
    spec: IndexSpec
    bands: list[BandFile] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def compatible(self) -> bool:
        return not self.problems and all(b.readable and not b.problems for b in self.bands)


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    return True


def _walk(root: Path, start: Path, max_depth: int):
    """(directory, file names) pairs below `start`, shallow and bounded. Links are not followed."""
    base_depth = len(start.parts)
    visited = 0
    for current, directories, files in os.walk(start, followlinks=False):
        visited += 1
        if visited > MAX_DIRECTORIES:
            return
        here = Path(current)
        if len(here.parts) - base_depth >= max_depth:
            directories[:] = []
        directories[:] = sorted(d for d in directories if not (here / d).is_symlink() and _within(root, here / d))
        yield here, sorted(files)


def scene_id_for(product_uri: str) -> str:
    return hashlib.sha256(product_uri.encode()).hexdigest()[:16]


def _describe(band: BandFile, role_scaling: indices.BandScaling) -> None:
    band.scaling = role_scaling.describe()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(band.path) as ds:
                band.width, band.height, band.bands, band.dtype = ds.width, ds.height, ds.count, ds.dtypes[0]
                band.crs = ds.crs.to_string() if ds.crs else None
                band.pixel_size = [abs(ds.transform.a), abs(ds.transform.e)]
                band.readable = True
                if ds.count != 1:
                    band.problems.append(f"The file has {ds.count} bands; one is expected.")
                if ds.dtypes[0] not in ("uint16", "int16", "uint8", "int32", "uint32"):
                    band.problems.append(f"The file holds {ds.dtypes[0]} values; integer digital numbers are expected.")
                if ds.crs is None or ds.transform.is_identity:
                    band.problems.append("The file has no georeferencing.")
    except (rasterio.errors.RasterioError, OSError):
        band.problems.append("The file could not be read as a raster.")


def _scene(root: Path, xml_path: Path, spec: IndexSpec) -> Scene | None:
    try:
        size = xml_path.stat().st_size
        if size > MAX_XML_BYTES:
            return None
        product = indices.read_product(xml_path.read_bytes())
    except (OSError, IndexInputError):
        return None
    scene = Scene(scene_id_for(product.product_uri), product, spec)
    names: dict[str, Path] = {}
    seen = 0
    for here, files in _walk(root, xml_path.parent, MAX_SCENE_DEPTH):
        for name in files:
            seen += 1
            path = here / name
            if name.lower().endswith(BAND_SUFFIXES) and not path.is_symlink() and name not in names:
                names[name] = path
        if seen > MAX_FILES_PER_SCENE:
            break
    for role, code in ((spec.first_role, spec.first_band), (spec.second_role, spec.second_band)):
        band = BandFile(role=role, band=code)
        scene.bands.append(band)
        try:
            scaling = indices.scaling_for(product, code)
        except IndexInputError as exc:
            band.problems.append(str(exc))
            continue
        reference = product.image_references.get(f"{code}_10m")
        if not reference:
            band.problems.append(f"The product metadata names no 10 m {code} image.")
            continue
        stem = reference.rsplit("/", 1)[-1]
        match = next((names[stem + s] for s in BAND_SUFFIXES if stem + s in names), None)
        if match is None or not _within(root, match):
            band.problems.append(f"No local file named {stem}.jp2 (or .tif) was found.")
            band.scaling = scaling.describe()
            continue
        band.path, band.file_name, band.found = match, match.name, True
        _describe(band, scaling)
    if all(b.readable and not b.problems for b in scene.bands):
        first, second = (b.path for b in scene.bands)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                with rasterio.open(first) as a, rasterio.open(second) as b:
                    scene.problems.extend(indices.check_inputs(a, b))
        except (rasterio.errors.RasterioError, OSError):
            scene.problems.append("The bands could not be compared.")
    return scene


def scan(root: Path | None, spec: IndexSpec) -> list[Scene]:
    """Every scene under `root` that has a readable product metadata file, newest acquisition first."""
    if root is None:
        return []
    try:
        base = Path(root).resolve(strict=True)
    except OSError:
        return []
    if not base.is_dir():
        return []
    scenes: list[Scene] = []
    for here, files in _walk(base, base, MAX_DEPTH):
        if PRODUCT_FILE in files and not (here / PRODUCT_FILE).is_symlink():
            scene = _scene(base, here / PRODUCT_FILE, spec)
            if scene is not None:
                scenes.append(scene)
            if len(scenes) >= MAX_SCENES:
                break
    scenes.sort(key=lambda s: (s.product.start_time or "") if s.product else "", reverse=True)
    return scenes


def find_scene(root: Path | None, spec: IndexSpec, scene_id: str) -> Scene | None:
    """A scene by the opaque id `scan` gave it. The caller never names a path."""
    return next((s for s in scan(root, spec) if s.scene_id == scene_id), None)
