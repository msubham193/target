"""Derived spectral indices from two aligned bands of one Sentinel-2 L2A product. NDVI is the only index registered; any
other normalised difference (NDWI, ...) reuses this engine by adding an `IndexSpec`.

What is computed, and how it stays honest:

* The scaling is read from the product's own metadata (MTD_MSIL2A.xml), band by band: reflectance =
  (DN + BOA_ADD_OFFSET[band]) / BOA_QUANTIFICATION_VALUE. Nothing is hardcoded; a product without them is refused.
* Digital numbers equal to the product's NODATA value (and its SATURATED value) are invalid in the result, as is any
  pixel that is invalid in either band. A near-zero or non-finite denominator also gives nodata. Valid results are never
  clipped: values outside [-1, 1] (possible where the offset makes a reflectance negative) are kept and counted.
* The two bands must already share one grid (size, CRS and geotransform, checked with `compare.check_alignment`).
  Nothing is resampled or reprojected, so the shared CRS transformation engine is neither needed nor bypassed; the
  output is in the inputs' CRS.
* Work is done in horizontal strips; neither band is ever held whole in memory. Inputs are only read.
* Display stretching is not applied here: the GeoTIFF holds the scientific float32 values.
"""

import hashlib
import math
import os
import re
import warnings
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

from geopoc.geo.compare import GEOTRANSFORM_TOLERANCE, check_alignment
from geopoc.geo.georeference import Effective

TOOL = "geopoc.derived_index"
TOOL_VERSION = "1"
OUTPUT_NODATA = -9999.0
OUTPUT_DTYPE = "float32"
DENOMINATOR_EPSILON = 1e-6  # reflectance units; the data step is 1e-4, so this only excludes an exactly zero sum
ROWS_PER_BLOCK = 512
OVERVIEW_FACTORS = (2, 4, 8, 16)
MAX_XML_BYTES = 2 * 1024 * 1024
HISTOGRAM_BINS = 20_000

ProgressFn = Callable[[float], None]


class IndexInputError(ValueError):
    """The inputs cannot produce this index. The message says why and is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- the index registry -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexSpec:
    """A normalised difference, (first - second) / (first + second), of two reflectance bands."""

    key: str
    title: str
    formula: str
    first_role: str
    first_band: str
    second_role: str
    second_band: str
    # Calculated values outside this inclusive range are masked as nodata (never clamped). None keeps every result.
    analytical_range: tuple[float, float] | None = None
    # The colour ramp (geo.colormaps) the map uses for this index; display only.
    colormap: str | None = None


INDICES: dict[str, IndexSpec] = {
    "ndvi": IndexSpec(
        key="ndvi",
        title="NDVI",
        formula="(NIR - Red) / (NIR + Red)",
        first_role="nir",
        first_band="B08",
        second_role="red",
        second_band="B04",
        analytical_range=(-1.0, 1.0),
        colormap="ndvi",
    ),
}


# --- product metadata -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BandScaling:
    physical_band: str  # as the product names it, e.g. "B4"
    band_id: int
    quantification: float
    add_offset: float
    nodata_dn: int
    saturated_dn: int | None

    def describe(self) -> dict[str, Any]:
        return {
            "band": self.physical_band,
            "band_id": self.band_id,
            "quantification_value": self.quantification,
            "add_offset": self.add_offset,
            "nodata_dn": self.nodata_dn,
            "saturated_dn": self.saturated_dn,
            "formula": "(DN + add_offset) / quantification_value",
        }


@dataclass(frozen=True)
class ProductInfo:
    product_uri: str
    tile_id: str | None
    start_time: str | None
    processing_baseline: str | None
    spacecraft: str | None
    processing_level: str
    metadata_sha256: str
    scalings: dict[str, BandScaling] = field(default_factory=dict)
    image_references: dict[str, str] = field(default_factory=dict)  # "B04_10m" -> path as written in the XML


def physical_name(code: str) -> str:
    """'B04' -> 'B4', 'B8A' -> 'B8A', as the product's Spectral_Information names bands."""
    match = re.fullmatch(r"B0?(\d{1,2})(A?)", code.strip().upper())
    if not match:
        raise IndexInputError("BAND_NAME", f"'{code}' is not a Sentinel-2 band name.")
    return f"B{int(match.group(1))}{match.group(2)}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_product(xml: bytes) -> ProductInfo:
    """Parse a Sentinel-2 L2A user-product metadata file (MTD_MSIL2A.xml)."""
    if len(xml) > MAX_XML_BYTES:
        raise IndexInputError("METADATA_TOO_LARGE", "The product metadata file is larger than expected.")
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise IndexInputError("METADATA_UNSAFE", "The product metadata declares entities, which are not accepted.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise IndexInputError("METADATA_INVALID", "The product metadata is not well-formed XML.") from exc
    if _local(root.tag) != "Level-2A_User_Product":
        raise IndexInputError("METADATA_KIND", "This is not a Sentinel-2 Level-2A product metadata file.")

    def first(name: str) -> str | None:
        for element in root.iter():
            if _local(element.tag) == name:
                return (element.text or "").strip() or None
        return None

    def number(text: str | None, what: str) -> float:
        try:
            value = float(text) if text is not None else math.nan
        except ValueError:
            value = math.nan
        if not math.isfinite(value):
            raise IndexInputError("METADATA_SCALING", f"The product metadata has no usable {what}.")
        return value

    uri = first("PRODUCT_URI")
    level = first("PROCESSING_LEVEL")
    if not uri or level != "Level-2A":
        raise IndexInputError(
            "METADATA_KIND", "The product is not Level-2A, so its reflectance scaling does not apply."
        )
    quantification = number(first("BOA_QUANTIFICATION_VALUE"), "BOA_QUANTIFICATION_VALUE")
    if quantification <= 0:
        raise IndexInputError("METADATA_SCALING", "BOA_QUANTIFICATION_VALUE must be positive.")

    special = {"NODATA": None, "SATURATED": None}
    for block in root.iter():
        if _local(block.tag) == "Special_Values":
            children = {_local(c.tag): (c.text or "").strip() for c in block}
            text, index = children.get("SPECIAL_VALUE_TEXT"), children.get("SPECIAL_VALUE_INDEX")
            if text in special and index is not None and index.lstrip("-").isdigit():
                special[text] = int(index)
    if special["NODATA"] is None:
        raise IndexInputError("METADATA_SCALING", "The product metadata does not declare its NODATA value.")

    ids = {
        e.attrib["physicalBand"]: int(e.attrib["bandId"])
        for e in root.iter()
        if _local(e.tag) == "Spectral_Information"
        and "physicalBand" in e.attrib
        and e.attrib.get("bandId", "").isdigit()
    }
    offsets = {
        int(e.attrib["band_id"]): number((e.text or "").strip(), f"BOA_ADD_OFFSET for band {e.attrib['band_id']}")
        for e in root.iter()
        if _local(e.tag) == "BOA_ADD_OFFSET" and e.attrib.get("band_id", "").isdigit()
    }
    scalings = {
        name: BandScaling(name, band_id, quantification, offsets[band_id], special["NODATA"], special["SATURATED"])
        for name, band_id in ids.items()
        if band_id in offsets
    }
    references = {}
    for e in root.iter():
        if _local(e.tag) == "IMAGE_FILE" and e.text:
            match = re.search(r"_(B\d{2}A?|B8A|TCI|AOT|WVP|SCL)_(\d+m)$", e.text.strip())
            if match:
                references[f"{match.group(1)}_{match.group(2)}"] = e.text.strip()
    tile = re.search(r"_(T\d{2}[A-Z]{3})_", uri)
    return ProductInfo(
        product_uri=uri,
        tile_id=tile.group(1) if tile else None,
        start_time=first("PRODUCT_START_TIME"),
        processing_baseline=first("PROCESSING_BASELINE"),
        spacecraft=first("SPACECRAFT_NAME"),
        processing_level=level,
        metadata_sha256=hashlib.sha256(xml).hexdigest(),
        scalings=scalings,
        image_references=references,
    )


def scaling_for(product: ProductInfo, band_code: str) -> BandScaling:
    name = physical_name(band_code)
    if name not in product.scalings:
        raise IndexInputError("METADATA_SCALING", f"The product metadata has no scaling entry for band {band_code}.")
    return product.scalings[name]


# --- reflectance and the normalised difference (pure array functions) -------------------------------------------------


def reflectance(dn: np.ndarray, scaling: BandScaling) -> tuple[np.ndarray, np.ndarray]:
    """(reflectance as float64, mask of usable pixels). NODATA and SATURATED digital numbers are not usable."""
    usable = dn != scaling.nodata_dn
    if scaling.saturated_dn is not None:
        usable &= dn != scaling.saturated_dn
    return (dn.astype("float64") + scaling.add_offset) / scaling.quantification, usable


def normalized_difference(
    first: np.ndarray, second: np.ndarray, usable: np.ndarray
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """(first - second) / (first + second) where `usable`. Returns (values, valid mask, near-zero denominators,
    non-finite results). Invalid pixels are NaN in `values`; valid values are not clipped."""
    total = first + second
    near_zero = usable & (np.abs(total) < DENOMINATOR_EPSILON)
    candidate = usable & ~near_zero
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        values = np.where(candidate, (first - second) / np.where(candidate, total, 1.0), np.nan)
    finite = np.isfinite(values)
    nonfinite = candidate & ~finite
    return values, candidate & finite, int(near_zero.sum()), int(nonfinite.sum())


# --- alignment --------------------------------------------------------------------------------------------------------


def file_state(ds: rasterio.DatasetReader) -> Effective:
    """A raster's own georeferencing in the form `check_alignment` takes."""
    crs = ds.crs
    epsg = crs.to_epsg() if crs else None
    return Effective(
        version=0,
        source="file",
        assertion_id=None,
        crs_id=f"EPSG:{epsg}" if epsg else None,
        crs_name=crs.to_wkt().split('"')[1] if crs else None,
        crs_wkt=crs.to_wkt() if crs else None,
        crs_usable=crs is not None,
        geotransform=list(ds.transform.to_gdal()),
        geotransform_usable=not ds.transform.is_identity,
        georeferenced=crs is not None and not ds.transform.is_identity,
        footprint=None,
        bounds_native=None,
        pixel_size=[abs(ds.transform.a), abs(ds.transform.e)],
        pixel_units=None,
    )


def check_inputs(first: rasterio.DatasetReader, second: rasterio.DatasetReader) -> list[str]:
    """Reasons the two bands cannot be combined (empty when they can)."""
    reasons: list[str] = []
    for label, ds in (("first", first), ("second", second)):
        if ds.count != 1:
            reasons.append(f"The {label} band file has {ds.count} bands; one is expected.")
        if np.dtype(ds.dtypes[0]).kind not in "ui":
            reasons.append(f"The {label} band file holds {ds.dtypes[0]} values; integer digital numbers are expected.")
    alignment = check_alignment(
        first.width, first.height, file_state(first), second.width, second.height, file_state(second)
    )
    return reasons + alignment.reasons


# --- processing -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BandInput:
    path: Path
    band: str  # as named by the caller, e.g. "B04"
    scaling: BandScaling


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _band_tags(role: str, band: BandInput, checksum: str) -> dict[str, str]:
    prefix = f"GEOPOC_{role.upper()}"
    scaling = band.scaling
    return {
        f"{prefix}_BAND": band.band,
        f"{prefix}_FILE": band.path.name,
        f"{prefix}_SHA256": checksum,
        f"{prefix}_SCALING": f"offset={scaling.add_offset:g};quantification={scaling.quantification:g}",
    }


def _tags(
    spec: IndexSpec,
    product: ProductInfo,
    first: BandInput,
    second: BandInput,
    checksums: dict[str, str],
    valid_range: tuple[float, float] | None,
) -> dict:
    scaling = first.scaling
    range_rule = (
        f" Calculated values outside [{valid_range[0]:g}, {valid_range[1]:g}] (inclusive) are also nodata; they are "
        "masked, never clamped, and are counted in the processing record."
        if valid_range
        else " Valid results are not clipped."
    )
    return {
        "GEOPOC_TOOL": TOOL,
        "GEOPOC_TOOL_VERSION": TOOL_VERSION,
        "GEOPOC_INDEX": spec.title,
        "GEOPOC_FORMULA": spec.formula,
        "GEOPOC_SCALING": "reflectance = (DN + BOA_ADD_OFFSET) / BOA_QUANTIFICATION_VALUE per band (product metadata)",
        **_band_tags(spec.first_role, first, checksums["first"]),
        **_band_tags(spec.second_role, second, checksums["second"]),
        "GEOPOC_PRODUCT_URI": product.product_uri,
        "GEOPOC_PRODUCT_METADATA_SHA256": product.metadata_sha256,
        "GEOPOC_ACQUISITION_START": product.start_time or "",
        "GEOPOC_PROCESSING_BASELINE": product.processing_baseline or "",
        "GEOPOC_SPACECRAFT": product.spacecraft or "",
        "GEOPOC_TILE": product.tile_id or "",
        "GEOPOC_NODATA_RULE": (
            f"nodata ({OUTPUT_NODATA:g}) where either input DN is {scaling.nodata_dn} (NODATA) or "
            f"{scaling.saturated_dn} (SATURATED), the denominator is below {DENOMINATOR_EPSILON:g}, or the result is "
            "not finite." + range_rule
        ),
        "GEOPOC_VALID_RANGE": f"{valid_range[0]:g},{valid_range[1]:g}" if valid_range else "none",
        "GEOPOC_NOTE": "Scientific float values. Display colour stretching is applied separately and not stored here.",
    }


def compute_index(
    spec: IndexSpec,
    product: ProductInfo,
    first: BandInput,
    second: BandInput,
    target: Path,
    *,
    progress: ProgressFn | None = None,
    overwrite: bool = False,
    valid_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Write the index as a tiled, compressed float32 GeoTIFF with overviews, and return its statistics.

    With `valid_range` (inclusive), calculated values outside it become nodata. They are masked, never clamped, and
    counted. Without it every finite result is kept.

    The inputs are only read. `target` is written under a temporary name and moved into place at the end, so a failed
    or cancelled run leaves no partial file; an existing file, or either input, is never overwritten."""
    target = Path(target)
    for source in (first.path, second.path):
        if target.resolve() == source.resolve():
            raise IndexInputError("OUTPUT_IS_INPUT", "The output would overwrite an input band.")
    if target.exists() and not overwrite:
        raise IndexInputError("OUTPUT_EXISTS", "The output file already exists and is not overwritten.")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)

    totals = {
        "pixels": 0,
        "valid": 0,
        "input_nodata": 0,
        "input_saturated": 0,
        "denominator_near_zero": 0,
        "non_finite": 0,
        "valid_before_range_mask": 0,
        "masked_out_of_range": 0,
        "outside_minus1_plus1": 0,
        "negative": 0,
    }
    total = total_sq = 0.0
    low, high = math.inf, -math.inf
    checksums = {"first": sha256_file(first.path), "second": sha256_file(second.path)}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.Env(GDAL_CACHEMAX=256), rasterio.open(first.path) as a, rasterio.open(second.path) as b:
                reasons = check_inputs(a, b)
                if reasons:
                    raise IndexInputError("INPUTS_INCOMPATIBLE", " ".join(reasons))
                profile = {
                    "driver": "GTiff",
                    "width": a.width,
                    "height": a.height,
                    "count": 1,
                    "dtype": OUTPUT_DTYPE,
                    "crs": a.crs,
                    "transform": a.transform,
                    "nodata": OUTPUT_NODATA,
                    "tiled": True,
                    "blockxsize": 256,
                    "blockysize": 256,
                    "compress": "DEFLATE",
                    "zlevel": 6,
                    "BIGTIFF": "IF_SAFER",
                }
                with rasterio.open(partial, "w", **profile) as out:
                    out.set_band_description(1, f"{spec.title} = {spec.formula} (scientific values, float32)")
                    out.update_tags(**_tags(spec, product, first, second, checksums, valid_range))
                    for row in range(0, a.height, ROWS_PER_BLOCK):
                        window = Window(0, row, a.width, min(ROWS_PER_BLOCK, a.height - row))
                        dn_first, dn_second = a.read(1, window=window), b.read(1, window=window)
                        refl_first, ok_first = reflectance(dn_first, first.scaling)
                        refl_second, ok_second = reflectance(dn_second, second.scaling)
                        values, valid, near_zero, non_finite = normalized_difference(
                            refl_first, refl_second, ok_first & ok_second
                        )
                        stored = values.astype(OUTPUT_DTYPE)  # the range is judged on the value that is stored
                        valid &= stored != OUTPUT_NODATA
                        totals["valid_before_range_mask"] += int(valid.sum())
                        if valid_range is not None:
                            outside = valid & ((stored < valid_range[0]) | (stored > valid_range[1]))
                            totals["masked_out_of_range"] += int(outside.sum())
                            valid &= ~outside
                        result = np.where(valid, values, OUTPUT_NODATA).astype(OUTPUT_DTYPE)
                        out.write(result, 1, window=window)
                        chosen = values[valid]
                        totals["pixels"] += dn_first.size
                        totals["valid"] += int(chosen.size)
                        totals["input_nodata"] += int(
                            ((dn_first == first.scaling.nodata_dn) | (dn_second == second.scaling.nodata_dn)).sum()
                        )
                        if first.scaling.saturated_dn is not None:
                            totals["input_saturated"] += int(
                                (
                                    (dn_first == first.scaling.saturated_dn)
                                    | (dn_second == second.scaling.saturated_dn)
                                ).sum()
                            )
                        totals["denominator_near_zero"] += near_zero
                        totals["non_finite"] += non_finite
                        if chosen.size:
                            totals["outside_minus1_plus1"] += int((np.abs(chosen) > 1.0).sum())
                            totals["negative"] += int((chosen < 0).sum())
                            total += float(chosen.sum())
                            total_sq += float((chosen * chosen).sum())
                            low, high = min(low, float(chosen.min())), max(high, float(chosen.max()))
                        if progress:
                            progress(0.9 * (row + window.height) / a.height)
                width, height, crs_wkt = a.width, a.height, a.crs.to_wkt()
        with rasterio.Env(COMPRESS_OVERVIEW="DEFLATE", GDAL_TIFF_OVR_BLOCKSIZE="256"):
            with rasterio.open(partial, "r+") as out:
                out.build_overviews(list(OVERVIEW_FACTORS), Resampling.average)
        if progress:
            progress(0.97)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    count = totals["valid"]
    mean = total / count if count else None
    return {
        "index": spec.key,
        "formula": spec.formula,
        "output": str(target),
        "width": width,
        "height": height,
        "crs_wkt": crs_wkt,
        "dtype": OUTPUT_DTYPE,
        "nodata": OUTPUT_NODATA,
        "valid_pixels": count,
        "invalid_pixels": totals["pixels"] - count,
        "pixels": totals["pixels"],
        "min": low if count else None,
        "max": high if count else None,
        "mean": mean,
        "std": math.sqrt(max(0.0, total_sq / count - mean * mean)) if count else None,
        "excluded": {
            "input_nodata": totals["input_nodata"],
            "input_saturated": totals["input_saturated"],
            "denominator_near_zero": totals["denominator_near_zero"],
            "non_finite_result": totals["non_finite"],
        },
        "range_mask": list(valid_range) if valid_range else None,
        "valid_before_range_mask": totals["valid_before_range_mask"],
        "masked_out_of_range": totals["masked_out_of_range"],
        "valid_outside_minus1_plus1": totals["outside_minus1_plus1"],
        "valid_negative": totals["negative"],
        "source_sha256": checksums,
        "tool": TOOL,
        "tool_version": TOOL_VERSION,
        "gdal_version": rasterio.__gdal_version__,
    }


def _overviews_ok(out: rasterio.DatasetReader) -> bool:
    """All four overview levels exist. A raster of 256 pixels or less is not held to this: GDAL rounds its levels."""
    return list(out.overviews(1)) == list(OVERVIEW_FACTORS) or max(out.width, out.height) <= 256


def _expected_strip(
    dn_first: np.ndarray,
    dn_second: np.ndarray,
    first: BandScaling,
    second: BandScaling,
    valid_range: tuple[float, float] | None,
) -> np.ndarray:
    """What a strip of the output should hold, computed again here only for validation (float32, nodata -9999)."""
    r1 = (dn_first.astype("float64") + first.add_offset) / first.quantification
    r2 = (dn_second.astype("float64") + second.add_offset) / second.quantification
    keep = (dn_first != first.nodata_dn) & (dn_second != second.nodata_dn)
    for dn, scaling in ((dn_first, first), (dn_second, second)):
        if scaling.saturated_dn is not None:
            keep &= dn != scaling.saturated_dn
    total = r1 + r2
    keep &= np.abs(total) >= DENOMINATOR_EPSILON
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ratio = ((r1 - r2) / np.where(keep, total, 1.0)).astype(OUTPUT_DTYPE)
    keep &= np.isfinite(ratio) & (ratio != OUTPUT_NODATA)
    if valid_range is not None:
        keep &= (ratio >= valid_range[0]) & (ratio <= valid_range[1])
    return np.where(keep, ratio, np.float32(OUTPUT_NODATA))


# --- validation of a written index (independent of the writer) --------------------------------------------------------


def validate_output(
    output: Path,
    first: BandInput,
    second: BandInput,
    summary: dict[str, Any],
    *,
    samples: int = 40,
    seed: int = 12345,
) -> dict[str, Any]:
    """Reopen the output and check it against the inputs and the summary. Returns the findings and a list of failures.

    Sample pixels are recomputed here in plain Python from the digital numbers, not with the writer's array code."""
    failures: list[str] = []
    report: dict[str, Any] = {}
    valid_range = tuple(summary["range_mask"]) if summary.get("range_mask") else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(output) as out, rasterio.open(first.path) as a, rasterio.open(second.path) as b:
            report["driver"], report["dtype"], report["bands"] = out.driver, out.dtypes[0], out.count
            report["size"] = [out.width, out.height]
            report["crs"] = out.crs.to_string() if out.crs else None
            report["transform"] = list(out.transform)[:6]
            report["bounds"] = list(out.bounds)
            report["nodata"] = out.nodata
            report["block_shapes"], report["overviews"] = out.block_shapes, out.overviews(1)
            report["compression"] = out.compression.value if out.compression else None
            report["tags"] = out.tags()
            checks = {
                "float32": out.dtypes[0] == "float32",
                "one band": out.count == 1,
                "same size as the inputs": (out.width, out.height) == (a.width, a.height),
                "same CRS as the inputs": out.crs == a.crs and out.crs == b.crs,
                "same bounds as the inputs": out.bounds == a.bounds,
                "geotransform equals the inputs'": all(
                    abs(x - y) <= GEOTRANSFORM_TOLERANCE * max(1.0, abs(x), abs(y))
                    for x, y in zip(out.transform.to_gdal(), a.transform.to_gdal(), strict=True)
                ),
                "explicit nodata value": out.nodata == OUTPUT_NODATA,
                "tiled": out.profile.get("tiled") is True,
                "overviews present": _overviews_ok(out),
            }
            valid_count = invalid_count = nan_count = inf_count = 0
            lows, highs = math.inf, -math.inf
            sums = sums_sq = 0.0
            for row in range(0, out.height, ROWS_PER_BLOCK):
                window = Window(0, row, out.width, min(ROWS_PER_BLOCK, out.height - row))
                data = out.read(1, window=window)
                nan_count += int(np.isnan(data).sum())
                inf_count += int(np.isinf(data).sum())
                valid = data != OUTPUT_NODATA
                chosen = data[valid & np.isfinite(data)].astype("float64")
                valid_count += int(chosen.size)
                invalid_count += int((~valid).sum())
                if chosen.size:
                    lows, highs = min(lows, float(chosen.min())), max(highs, float(chosen.max()))
                    sums += float(chosen.sum())
                    sums_sq += float((chosen * chosen).sum())
            mean = sums / valid_count if valid_count else None
            report["valid_pixels"], report["invalid_pixels"] = valid_count, invalid_count
            report["min"], report["max"], report["mean"] = (lows, highs, mean) if valid_count else (None, None, None)
            report["std"] = math.sqrt(max(0.0, sums_sq / valid_count - mean * mean)) if valid_count else None
            report["nan_pixels"], report["infinite_pixels"] = nan_count, inf_count
            checks["no NaN or infinite pixels"] = nan_count == 0 and inf_count == 0
            if valid_range:
                report["valid_range"] = list(valid_range)
                checks[f"no stored value lies outside [{valid_range[0]:g}, {valid_range[1]:g}]"] = not valid_count or (
                    lows >= valid_range[0] and highs <= valid_range[1]
                )
                checks["valid + masked out-of-range equals the pixels valid before the range mask"] = (
                    valid_count + summary["masked_out_of_range"] == summary["valid_before_range_mask"]
                )
            checks["valid count equals the writer's"] = valid_count == summary["valid_pixels"]
            checks["invalid count equals the writer's"] = invalid_count == summary["invalid_pixels"]
            checks["statistics equal the writer's"] = bool(
                valid_count
                and math.isclose(lows, summary["min"], abs_tol=1e-6)
                and math.isclose(highs, summary["max"], abs_tol=1e-6)
                and math.isclose(mean, summary["mean"], abs_tol=1e-6)
            )
            if valid_count and highs > lows:
                counts = np.zeros(HISTOGRAM_BINS, dtype=np.int64)
                step = (highs - lows) / HISTOGRAM_BINS
                for row in range(0, out.height, ROWS_PER_BLOCK):
                    window = Window(0, row, out.width, min(ROWS_PER_BLOCK, out.height - row))
                    data = out.read(1, window=window)
                    chosen = data[(data != OUTPUT_NODATA) & np.isfinite(data)].astype("float64")
                    index = np.clip(((chosen - lows) / step).astype(np.int64), 0, HISTOGRAM_BINS - 1)
                    counts += np.bincount(index, minlength=HISTOGRAM_BINS)
                cumulative = np.cumsum(counts)
                report["percentiles"] = {
                    str(p): float(lows + (int(np.searchsorted(cumulative, p / 100 * valid_count)) + 0.5) * step)
                    for p in (1, 5, 25, 50, 75, 95, 99)
                }
                report["percentile_resolution"] = float(step)

            rng = np.random.default_rng(seed)
            last_col, last_row = out.width - 1, out.height - 1
            points = [(0, 0), (last_col, 0), (0, last_row), (last_col, last_row)]
            points += [
                (int(c), int(r))
                for c, r in zip(rng.integers(0, out.width, samples), rng.integers(0, out.height, samples), strict=True)
            ]
            worst, compared, expected_invalid, invalid_agree = 0.0, 0, 0, 0
            for col, line in points:
                pixel = Window(col, line, 1, 1)
                dn_first, dn_second = int(a.read(1, window=pixel)[0, 0]), int(b.read(1, window=pixel)[0, 0])
                got = float(out.read(1, window=pixel)[0, 0])
                r1 = (dn_first + first.scaling.add_offset) / first.scaling.quantification
                r2 = (dn_second + second.scaling.add_offset) / second.scaling.quantification
                unusable = (
                    dn_first in (first.scaling.nodata_dn, first.scaling.saturated_dn)
                    or dn_second in (second.scaling.nodata_dn, second.scaling.saturated_dn)
                    or abs(r1 + r2) < DENOMINATOR_EPSILON
                )
                if not unusable and valid_range:
                    stored = float(np.float32((r1 - r2) / (r1 + r2)))
                    unusable = not valid_range[0] <= stored <= valid_range[1]  # masked, not clamped
                if unusable:
                    expected_invalid += 1
                    invalid_agree += int(got == OUTPUT_NODATA)
                    continue
                worst = max(worst, abs(got - (r1 - r2) / (r1 + r2)))
                compared += 1
            report["sample_pixels_compared"], report["sample_max_abs_difference"] = compared, worst
            report["sample_invalid_pixels_checked"] = expected_invalid
            checks["sample pixels match an independent calculation (|diff| <= 1e-6)"] = compared > 0 and worst <= 1e-6
            checks["sample invalid pixels are nodata in the output"] = invalid_agree == expected_invalid

            # Whole strips (top, middle, bottom) compared pixel by pixel with a second implementation of the arithmetic.
            strip_total = strip_bad = strip_masked = 0
            for start in sorted(
                {0, (out.height // 2 // ROWS_PER_BLOCK) * ROWS_PER_BLOCK, max(0, out.height - ROWS_PER_BLOCK)}
            ):
                window = Window(0, start, out.width, min(ROWS_PER_BLOCK, out.height - start))
                dn_first, dn_second = a.read(1, window=window), b.read(1, window=window)
                expected = _expected_strip(dn_first, dn_second, first.scaling, second.scaling, valid_range)
                got = out.read(1, window=window)
                strip_total += got.size
                strip_bad += int(((got == OUTPUT_NODATA) != (expected == OUTPUT_NODATA)).sum())
                both = (got != OUTPUT_NODATA) & (expected != OUTPUT_NODATA)
                strip_bad += int((np.abs(got[both].astype("float64") - expected[both]) > 1e-6).sum())
                if valid_range:  # how many pixels of this strip only the range mask removed
                    unmasked = _expected_strip(dn_first, dn_second, first.scaling, second.scaling, None)
                    strip_masked += int((unmasked != OUTPUT_NODATA).sum() - (expected != OUTPUT_NODATA).sum())
            report["strip_pixels_compared"], report["strip_mismatches"] = strip_total, strip_bad
            report["strip_pixels_masked_by_range"] = strip_masked
            checks["whole strips equal a second implementation of the arithmetic"] = strip_bad == 0
    report["checks"] = checks
    failures = [name for name, ok in checks.items() if not ok]
    report["failures"] = failures
    return report
