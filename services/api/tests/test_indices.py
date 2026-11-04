"""Derived spectral indices (geopoc.geo.indices): product-metadata scaling, the normalised difference, windowed
processing, the GeoTIFF that is written, and the checks that reopen it. Synthetic rasters in temporary folders only:
no network, no database writes, nothing outside tmp_path."""

import hashlib
import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine

from geopoc.geo import indices
from geopoc.geo.indices import (
    INDICES,
    IndexInputError,
    compute_index,
    normalized_difference,
    physical_name,
    read_product,
    reflectance,
    scaling_for,
    validate_output,
)

TRANSFORM = Affine(10.0, 0.0, 699960.0, 0.0, -10.0, 5700040.0)
CRS_UTM = CRS.from_epsg(32755)


def product_xml(*, red_offset="-1000", nir_offset="-500", quantification="10000", level="Level-2A", extra="") -> bytes:
    """A minimal L2A user-product metadata file. The red and near-infrared offsets differ on purpose."""
    offsets = ""
    if red_offset is not None:
        offsets += f'<BOA_ADD_OFFSET band_id="3">{red_offset}</BOA_ADD_OFFSET>'
    if nir_offset is not None:
        offsets += f'<BOA_ADD_OFFSET band_id="7">{nir_offset}</BOA_ADD_OFFSET>'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<n1:Level-2A_User_Product xmlns:n1="https://psd-14.sentinel2.eo.esa.int/PSD/User_Product_Level-2A.xsd">
 <n1:General_Info><Product_Info>
  <PRODUCT_START_TIME>2024-01-15T23:52:21.024Z</PRODUCT_START_TIME>
  <PRODUCT_URI>S2A_MSIL2A_20240115T235221_N0510_R130_T55HGS_20240116T021554.SAFE</PRODUCT_URI>
  <PROCESSING_LEVEL>{level}</PROCESSING_LEVEL>
  <PROCESSING_BASELINE>05.10</PROCESSING_BASELINE>
  <Datatake><SPACECRAFT_NAME>Sentinel-2A</SPACECRAFT_NAME></Datatake>
  <Product_Organisation><Granule_List><Granule imageFormat="JPEG2000">
   <IMAGE_FILE>GRANULE/L2A_T55HGS_A044743_20240115T235222/IMG_DATA/R10m/T55HGS_20240115T235221_B04_10m</IMAGE_FILE>
   <IMAGE_FILE>GRANULE/L2A_T55HGS_A044743_20240115T235222/IMG_DATA/R10m/T55HGS_20240115T235221_B08_10m</IMAGE_FILE>
  </Granule></Granule_List></Product_Organisation>
 </Product_Info>
 <Product_Image_Characteristics>
  <Special_Values><SPECIAL_VALUE_TEXT>NODATA</SPECIAL_VALUE_TEXT><SPECIAL_VALUE_INDEX>0</SPECIAL_VALUE_INDEX></Special_Values>
  <Special_Values><SPECIAL_VALUE_TEXT>SATURATED</SPECIAL_VALUE_TEXT><SPECIAL_VALUE_INDEX>65535</SPECIAL_VALUE_INDEX></Special_Values>
  <QUANTIFICATION_VALUES_LIST><BOA_QUANTIFICATION_VALUE unit="none">{quantification}</BOA_QUANTIFICATION_VALUE></QUANTIFICATION_VALUES_LIST>
  <BOA_ADD_OFFSET_VALUES_LIST>{offsets}</BOA_ADD_OFFSET_VALUES_LIST>
  <Spectral_Information_List>
   <Spectral_Information bandId="3" physicalBand="B4"><RESOLUTION>10</RESOLUTION></Spectral_Information>
   <Spectral_Information bandId="7" physicalBand="B8"><RESOLUTION>10</RESOLUTION></Spectral_Information>
  </Spectral_Information_List>
 </Product_Image_Characteristics></n1:General_Info>{extra}
</n1:Level-2A_User_Product>""".encode()


def write_band(path: Path, data: np.ndarray, *, crs=CRS_UTM, transform=TRANSFORM, dtype="uint16", count=1) -> Path:
    profile = {"driver": "GTiff", "width": data.shape[1], "height": data.shape[0], "count": count, "dtype": dtype}
    with rasterio.open(path, "w", crs=crs, transform=transform, **profile) as dst:
        for band in range(count):
            dst.write(data.astype(dtype), band + 1)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def product():
    return read_product(product_xml())


@pytest.fixture
def scene(tmp_path, product):
    """Red and NIR digital numbers on one 150 x 300 grid with nodata, a saturated pixel and a zero denominator."""
    rng = np.random.default_rng(7)
    height, width = 150, 300  # not a multiple of the strip height used below
    red = rng.integers(1100, 4000, (height, width)).astype("uint16")
    nir = rng.integers(1100, 6000, (height, width)).astype("uint16")
    red[0, 0], nir[5, 5] = 0, 0  # NODATA in one band only
    red[10, 10] = 65535  # SATURATED
    red[20, 20], nir[20, 20] = 1000, 1500  # red reflectance 0, NIR 0.1 -> a plain value of 1.0 in these offsets
    red[30, 30], nir[30, 30] = 1000, 500  # red 0.0, nir 0.0 -> exactly zero sum with offsets -1000 / -500
    red[40, 40], nir[40, 40] = 900, 1100  # negative red reflectance, positive nir
    red_path = write_band(tmp_path / "T55HGS_20240115T235221_B04_10m.tif", red)
    nir_path = write_band(tmp_path / "T55HGS_20240115T235221_B08_10m.tif", nir)
    spec = INDICES["ndvi"]
    return {
        "spec": spec,
        "red": indices.BandInput(red_path, "B04", scaling_for(product, "B04")),
        "nir": indices.BandInput(nir_path, "B08", scaling_for(product, "B08")),
        "red_dn": red,
        "nir_dn": nir,
        "dir": tmp_path,
    }


def expected_ndvi(red_dn, nir_dn, red_offset=-1000.0, nir_offset=-500.0, q=10000.0):
    r = (red_dn.astype("float64") + red_offset) / q
    n = (nir_dn.astype("float64") + nir_offset) / q
    with np.errstate(all="ignore"):
        return (n - r) / (n + r), r, n


# --- product metadata -------------------------------------------------------------------------------------------------


def test_scaling_is_read_per_band_from_the_product_metadata(product):
    red, nir = scaling_for(product, "B04"), scaling_for(product, "B08")
    assert (red.physical_band, red.band_id, red.add_offset, red.quantification) == ("B4", 3, -1000.0, 10000.0)
    assert (nir.physical_band, nir.band_id, nir.add_offset, nir.quantification) == ("B8", 7, -500.0, 10000.0)
    assert (red.nodata_dn, red.saturated_dn) == (0, 65535)


def test_product_identity_is_read_from_the_metadata(product):
    assert product.product_uri.endswith("T55HGS_20240116T021554.SAFE")
    assert (product.tile_id, product.start_time, product.processing_baseline) == (
        "T55HGS",
        "2024-01-15T23:52:21.024Z",
        "05.10",
    )
    assert product.spacecraft == "Sentinel-2A" and len(product.metadata_sha256) == 64
    assert product.image_references["B04_10m"].endswith("R10m/T55HGS_20240115T235221_B04_10m")


@pytest.mark.parametrize(
    ("code", "expected"), [("B04", "B4"), ("b08", "B8"), ("B8A", "B8A"), ("B12", "B12"), ("B01", "B1")]
)
def test_band_names_map_to_the_products_physical_names(code, expected):
    assert physical_name(code) == expected


@pytest.mark.parametrize("bad", ["", "red", "B", "B100", "TCI", "4"])
def test_a_bad_band_name_is_refused(bad):
    with pytest.raises(IndexInputError):
        physical_name(bad)


def test_a_band_without_a_scaling_entry_is_refused_not_defaulted(product):
    with pytest.raises(IndexInputError) as caught:
        scaling_for(product, "B11")
    assert caught.value.code == "METADATA_SCALING"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"red_offset": None}, None),  # no offset for the red band: that band has no scaling
        ({"quantification": "0"}, "METADATA_SCALING"),
        ({"quantification": "abc"}, "METADATA_SCALING"),
        ({"level": "Level-1C"}, "METADATA_KIND"),
    ],
)
def test_unusable_product_metadata_is_refused(kwargs, code):
    if code is None:
        info = read_product(product_xml(**kwargs))
        with pytest.raises(IndexInputError):
            scaling_for(info, "B04")
        assert scaling_for(info, "B08").add_offset == -500.0  # the other band is unaffected
    else:
        with pytest.raises(IndexInputError) as caught:
            read_product(product_xml(**kwargs))
        assert caught.value.code == code


def test_hostile_or_malformed_metadata_is_refused():
    for blob in (b"<a", b"not xml", b'<!DOCTYPE x [<!ENTITY e "x">]><a/>', b"<root/>", b"x" * (3 * 1024 * 1024)):
        with pytest.raises(IndexInputError):
            read_product(blob)


# --- reflectance and the normalised difference ------------------------------------------------------------------------


def test_reflectance_uses_the_bands_own_offset_and_masks_nodata_and_saturation(product):
    dn = np.array([[0, 1000, 1500, 65535, 900]], dtype="uint16")
    red, usable_red = reflectance(dn, scaling_for(product, "B04"))
    nir, usable_nir = reflectance(dn, scaling_for(product, "B08"))
    assert usable_red.tolist() == [[False, True, True, False, True]] == usable_nir.tolist()
    assert red[0, 2] == pytest.approx(0.05) and nir[0, 2] == pytest.approx(0.1)  # same DN, different band offsets
    assert red[0, 4] == pytest.approx(-0.01)  # a dark pixel gives a negative reflectance, which is kept


def test_ndvi_values_and_negative_results_are_preserved():
    nir = np.array([[0.05, 0.10, 0.30]])
    red = np.array([[0.20, 0.10, 0.10]])
    values, valid, near_zero, non_finite = normalized_difference(nir, red, np.ones_like(nir, dtype=bool))
    assert values.tolist()[0] == pytest.approx([-0.6, 0.0, 0.5])
    assert valid.all() and (near_zero, non_finite) == (0, 0)


def test_a_zero_denominator_is_nodata_and_counted():
    nir = np.array([[0.1, 0.0, 0.2]])
    red = np.array([[0.1, 0.0, -0.2]])
    values, valid, near_zero, _ = normalized_difference(nir, red, np.ones_like(nir, dtype=bool))
    assert valid.tolist() == [[True, False, False]] and near_zero == 2
    assert np.isnan(values[0, 1]) and np.isnan(values[0, 2])


def test_nan_and_infinite_inputs_never_become_valid_results():
    nir = np.array([[np.nan, np.inf, 0.4, -np.inf]])
    red = np.array([[0.1, 0.1, np.nan, 0.1]])
    values, valid, _, non_finite = normalized_difference(nir, red, np.ones_like(nir, dtype=bool))
    assert not valid.any() and non_finite == 4 and np.isnan(values).all()


def test_results_outside_minus_one_to_one_are_kept_not_clipped():
    nir = np.array([[0.002]])
    red = np.array([[-0.001]])  # a small positive sum with a negative term: |NDVI| = 3
    values, valid, _, _ = normalized_difference(nir, red, np.ones_like(nir, dtype=bool))
    assert valid[0, 0] and values[0, 0] == pytest.approx(3.0)


def test_unusable_pixels_stay_unusable():
    nir = np.array([[0.3, 0.3]])
    red = np.array([[0.1, 0.1]])
    _, valid, near_zero, _ = normalized_difference(nir, red, np.array([[True, False]]))
    assert valid.tolist() == [[True, False]] and near_zero == 0


# --- compatibility ----------------------------------------------------------------------------------------------------


def run(scene, target_name="out.tif", **kwargs):
    return compute_index(
        scene["spec"], read_product(product_xml()), scene["nir"], scene["red"], scene["dir"] / target_name, **kwargs
    )


def test_bands_on_different_grids_are_refused_and_leave_nothing(scene, tmp_path):
    cases = {
        "size": write_band(tmp_path / "size.tif", scene["nir_dn"][:-1, :]),
        "crs": write_band(tmp_path / "crs.tif", scene["nir_dn"], crs=CRS.from_epsg(32756)),
        "origin": write_band(tmp_path / "origin.tif", scene["nir_dn"], transform=TRANSFORM * Affine.translation(1, 0)),
        "pixel size": write_band(
            tmp_path / "pixel.tif", scene["nir_dn"], transform=Affine(20.0, 0, 699960.0, 0, -20.0, 5700040.0)
        ),
        "bands": write_band(tmp_path / "three.tif", scene["nir_dn"], count=3),
        "float": write_band(tmp_path / "float.tif", scene["nir_dn"], dtype="float32"),
    }
    for name, path in cases.items():
        bad = indices.BandInput(path, "B08", scene["nir"].scaling)
        with pytest.raises(IndexInputError) as caught:
            compute_index(scene["spec"], read_product(product_xml()), bad, scene["red"], tmp_path / f"{name}-out.tif")
        assert caught.value.code == "INPUTS_INCOMPATIBLE", name
        assert not list(tmp_path.glob(f"{name}-out.tif*")), name  # no output and no partial file


def test_an_input_or_an_existing_output_is_never_overwritten(scene):
    before = {k: sha(scene[k].path) for k in ("red", "nir")}
    for target in (scene["red"].path, scene["nir"].path):
        with pytest.raises(IndexInputError) as caught:
            compute_index(scene["spec"], read_product(product_xml()), scene["nir"], scene["red"], target)
        assert caught.value.code == "OUTPUT_IS_INPUT"
    existing = scene["dir"] / "existing.tif"
    existing.write_bytes(b"keep me")
    with pytest.raises(IndexInputError) as caught:
        run(scene, "existing.tif")
    assert caught.value.code == "OUTPUT_EXISTS" and existing.read_bytes() == b"keep me"
    assert before == {k: sha(scene[k].path) for k in ("red", "nir")}


# --- the computation --------------------------------------------------------------------------------------------------


def test_the_written_raster_matches_an_independent_calculation_everywhere(scene, monkeypatch):
    monkeypatch.setattr(indices, "ROWS_PER_BLOCK", 64)  # 150 rows -> three strips, the last one short
    ticks = []
    summary = run(scene, progress=ticks.append)
    strips = ticks[:-1]  # the last tick (0.97) follows the overviews
    assert len(strips) == math.ceil(150 / 64) and ticks == sorted(ticks) and ticks[-1] < 1.0  # in strips
    expected, r, n = expected_ndvi(scene["red_dn"], scene["nir_dn"])
    unusable = (scene["red_dn"] == 0) | (scene["nir_dn"] == 0) | (scene["red_dn"] == 65535) | (np.abs(n + r) < 1e-6)
    with rasterio.open(scene["dir"] / "out.tif") as out:
        data = out.read(1)
        assert out.dtypes == ("float32",) and out.nodata == -9999.0 and out.count == 1
    assert (data[unusable] == -9999.0).all()
    assert np.allclose(data[~unusable], expected[~unusable], atol=1e-6)
    assert summary["valid_pixels"] == int((~unusable).sum()) and summary["pixels"] == 150 * 300
    assert summary["valid_pixels"] + summary["invalid_pixels"] == summary["pixels"]


def test_named_pixels_behave_as_the_specification_says(scene):
    run(scene)
    with rasterio.open(scene["dir"] / "out.tif") as out:
        data = out.read(1)
    assert data[0, 0] == -9999.0  # red DN 0: NODATA
    assert data[5, 5] == -9999.0  # nir DN 0: NODATA
    assert data[10, 10] == -9999.0  # red DN 65535: SATURATED
    assert data[20, 20] == pytest.approx(1.0)  # (0.1 - 0.0) / (0.1 + 0.0)
    assert data[30, 30] == -9999.0  # zero denominator
    r, n = (900 - 1000) / 10000, (1100 - 500) / 10000
    assert data[40, 40] == pytest.approx((n - r) / (n + r)) and data[40, 40] > 0  # negative red reflectance kept


def test_the_summary_counts_every_reason_a_pixel_is_excluded(scene):
    summary = run(scene)
    excluded = summary["excluded"]
    assert excluded["input_nodata"] == 2 and excluded["input_saturated"] == 1 and excluded["denominator_near_zero"] >= 1
    assert excluded["non_finite_result"] == 0
    assert summary["valid_negative"] > 0 and summary["min"] < 0 < summary["max"]


def test_the_geotiff_keeps_the_grid_and_records_provenance(scene):
    summary = run(scene)
    with rasterio.open(scene["dir"] / "out.tif") as out:
        assert out.crs == CRS_UTM and out.transform == TRANSFORM and (out.width, out.height) == (300, 150)
        assert out.profile["tiled"] and (out.block_shapes[0]) == (256, 256) and out.compression.value == "DEFLATE"
        assert out.overviews(1) == [2, 4, 8, 16]
        tags = out.tags()
    assert tags["GEOPOC_FORMULA"] == "(NIR - Red) / (NIR + Red)" and tags["GEOPOC_INDEX"] == "NDVI"
    assert tags["GEOPOC_RED_FILE"] == scene["red"].path.name and tags["GEOPOC_NIR_FILE"] == scene["nir"].path.name
    assert tags["GEOPOC_RED_SHA256"] == sha(scene["red"].path) == summary["source_sha256"]["second"]
    assert tags["GEOPOC_NIR_SHA256"] == sha(scene["nir"].path) == summary["source_sha256"]["first"]
    assert tags["GEOPOC_RED_SCALING"] == "offset=-1000;quantification=10000"
    assert tags["GEOPOC_NIR_SCALING"] == "offset=-500;quantification=10000"  # per band, not one shared constant
    assert tags["GEOPOC_ACQUISITION_START"] == "2024-01-15T23:52:21.024Z" and tags["GEOPOC_TILE"] == "T55HGS"
    assert tags["GEOPOC_PRODUCT_URI"].endswith(".SAFE") and len(tags["GEOPOC_PRODUCT_METADATA_SHA256"]) == 64
    assert "not clipped" in tags["GEOPOC_NODATA_RULE"] and "-9999" in tags["GEOPOC_NODATA_RULE"]


def test_overviews_exclude_nodata_instead_of_averaging_it_in(scene):
    run(scene)
    with rasterio.open(scene["dir"] / "out.tif", OVERVIEW_LEVEL=0) as out:
        coarse = out.read(1)
    assert coarse.shape == (75, 150)
    valid = coarse[coarse != -9999.0]
    assert valid.size and valid.min() > -50 and valid.max() < 50  # a -9999 averaged in would be thousands off


def test_the_inputs_are_unchanged_and_no_partial_file_remains(scene):
    before = {k: sha(scene[k].path) for k in ("red", "nir")}
    run(scene)
    assert before == {k: sha(scene[k].path) for k in ("red", "nir")}
    assert sorted(p.name for p in scene["dir"].glob("out.tif*")) == ["out.tif"]


def test_a_failure_part_way_removes_the_partial_output(scene, monkeypatch):
    monkeypatch.setattr(indices, "ROWS_PER_BLOCK", 64)

    def stop(fraction):
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError):
        run(scene, progress=stop)
    assert not list(scene["dir"].glob("out.tif*"))


# --- validation of the written file -----------------------------------------------------------------------------------


def test_validation_passes_on_a_good_output(scene):
    summary = run(scene)
    report = validate_output(scene["dir"] / "out.tif", scene["nir"], scene["red"], summary, samples=60)
    assert report["failures"] == [] and report["nan_pixels"] == 0 and report["infinite_pixels"] == 0
    assert report["valid_pixels"] == summary["valid_pixels"] and report["sample_pixels_compared"] > 0
    assert report["crs"] == "EPSG:32755" and report["overviews"] == [2, 4, 8, 16]
    assert set(report["percentiles"]) == {"1", "5", "25", "50", "75", "95", "99"}


def test_validation_notices_a_corrupted_output(scene):
    summary = run(scene)
    with rasterio.open(scene["dir"] / "out.tif", "r+") as out:
        data = out.read(1)
        data[60, 60] = 0.123456  # a wrong value in a valid pixel
        data[61, 61] = np.nan  # and a NaN
        out.write(data, 1)
    report = validate_output(scene["dir"] / "out.tif", scene["nir"], scene["red"], summary, samples=2000, seed=1)
    assert report["failures"], "the corruption should have been detected"
    assert "no NaN or infinite pixels" in report["failures"]


def test_the_index_registry_has_ndvi_only_and_its_formula_is_nir_minus_red():
    assert list(INDICES) == ["ndvi"]
    spec = INDICES["ndvi"]
    assert (spec.first_role, spec.first_band, spec.second_role, spec.second_band) == ("nir", "B08", "red", "B04")


# --- the analytical range mask (values outside [-1, 1] become nodata; they are never clamped) -------------------------------

# (row, col): (red DN, nir DN) with the fixture product's offsets (red -1000, nir -500, quantification 10000)
DESIGNED = {
    (1, 1): (1500, 500),  # red 0.05, nir 0.00  -> exactly -1.0: inside the inclusive range, kept
    (1, 3): (1000, 1500),  # red 0.00, nir 0.10  -> exactly +1.0: kept
    (3, 1): (900, 700),  # red -0.01, nir 0.02 -> +3.0: masked
    (3, 3): (700, 600),  # red -0.03, nir 0.01 -> -2.0: masked
    (5, 1): (1500, 1000),  # red 0.05, nir 0.05  -> 0.0: kept
    (5, 3): (2000, 1100),  # red 0.10, nir 0.06  -> -0.25: a negative value inside the range, kept
    (7, 1): (0, 1500),  # NODATA in red
    (7, 3): (1000, 500),  # both reflectances 0: the denominator is zero
}


@pytest.fixture
def designed(tmp_path, product):
    red = np.full((9, 6), 1500, dtype="uint16")
    nir = np.full((9, 6), 1500, dtype="uint16")  # (0.10 - 0.05) / 0.15 = 0.333
    for (row, col), (r, n) in DESIGNED.items():
        red[row, col], nir[row, col] = r, n
    red_path = write_band(tmp_path / "red.tif", red)
    nir_path = write_band(tmp_path / "nir.tif", nir)
    return {
        "spec": INDICES["ndvi"],
        "red": indices.BandInput(red_path, "B04", scaling_for(product, "B04")),
        "nir": indices.BandInput(nir_path, "B08", scaling_for(product, "B08")),
        "dir": tmp_path,
        "product": product,
    }


def make(designed, name, valid_range, **kwargs):
    return compute_index(
        designed["spec"], designed["product"], designed["nir"], designed["red"], designed["dir"] / name,
        valid_range=valid_range, **kwargs,
    )  # fmt: skip


def read(designed, name):
    with rasterio.open(designed["dir"] / name) as out:
        return out.read(1)


def test_ndvi_is_registered_with_the_inclusive_analytical_range():
    assert INDICES["ndvi"].analytical_range == (-1.0, 1.0)


def test_without_a_range_every_finite_result_is_kept(designed):
    summary = make(designed, "raw.tif", None)
    data = read(designed, "raw.tif")
    assert data[3, 1] == pytest.approx(3.0) and data[3, 3] == pytest.approx(-2.0)
    assert summary["range_mask"] is None and summary["masked_out_of_range"] == 0
    assert summary["valid_outside_minus1_plus1"] == 2


def test_values_outside_the_range_are_masked_and_the_bounds_are_inclusive(designed):
    summary = make(designed, "final.tif", (-1.0, 1.0))
    data = read(designed, "final.tif")
    assert data[1, 1] == -1.0 and data[1, 3] == 1.0  # exactly on the bounds: valid
    assert data[3, 1] == -9999.0 and data[3, 3] == -9999.0  # +3 and -2: nodata, not +1 / -1
    assert data[5, 1] == 0.0 and data[5, 3] == pytest.approx(-0.25)  # zero and a negative inside the range
    assert data[7, 1] == -9999.0 and data[7, 3] == -9999.0  # nodata input and a zero denominator
    assert data[0, 0] == pytest.approx(1 / 3)  # everything else is untouched
    assert summary["masked_out_of_range"] == 2 and summary["range_mask"] == [-1.0, 1.0]


def test_masking_never_clamps(designed):
    make(designed, "final.tif", (-1.0, 1.0))
    data = read(designed, "final.tif")
    # only the two designed pixels sit exactly on a bound; masked values did not turn into +1 or -1
    assert int((data == 1.0).sum()) == 1 and int((data == -1.0).sum()) == 1
    valid = data[data != -9999.0]
    assert valid.min() == -1.0 and valid.max() == 1.0


def test_counts_add_up_and_the_other_exclusions_are_not_confused_with_masking(designed):
    summary = make(designed, "final.tif", (-1.0, 1.0))
    assert summary["valid_before_range_mask"] == summary["valid_pixels"] + summary["masked_out_of_range"]
    assert summary["valid_pixels"] + summary["invalid_pixels"] == summary["pixels"] == 54
    assert summary["excluded"]["input_nodata"] == 1 and summary["excluded"]["denominator_near_zero"] == 1
    assert summary["valid_outside_minus1_plus1"] == 0  # none is left outside after masking
    assert summary["invalid_pixels"] == 1 + 1 + 2  # nodata + zero denominator + the two masked


def test_the_final_equals_the_raw_with_out_of_range_set_to_nodata(designed):
    make(designed, "raw.tif", None)
    make(designed, "final.tif", (-1.0, 1.0))
    raw, final = read(designed, "raw.tif"), read(designed, "final.tif")
    expected = np.where((raw != -9999.0) & ((raw < -1.0) | (raw > 1.0)), np.float32(-9999.0), raw)
    assert np.array_equal(final, expected)


def test_the_mask_does_not_depend_on_the_strip_size(designed, monkeypatch):
    make(designed, "big.tif", (-1.0, 1.0))
    monkeypatch.setattr(indices, "ROWS_PER_BLOCK", 2)
    make(designed, "small.tif", (-1.0, 1.0))
    assert np.array_equal(read(designed, "big.tif"), read(designed, "small.tif"))


def test_the_range_is_recorded_in_the_file_and_validation_passes(designed):
    summary = make(designed, "final.tif", (-1.0, 1.0))
    with rasterio.open(designed["dir"] / "final.tif") as out:
        tags = out.tags()
    assert tags["GEOPOC_VALID_RANGE"] == "-1,1" and "masked, never clamped" in tags["GEOPOC_NODATA_RULE"]
    report = validate_output(designed["dir"] / "final.tif", designed["nir"], designed["red"], summary, samples=200)
    assert report["failures"] == []
    assert report["strip_mismatches"] == 0 and report["strip_pixels_masked_by_range"] == 2


def test_validation_notices_a_value_that_was_left_outside_the_range(designed):
    summary = make(designed, "final.tif", (-1.0, 1.0))
    with rasterio.open(designed["dir"] / "final.tif", "r+") as out:
        data = out.read(1)
        data[3, 1] = 3.0  # an out-of-range value that should have been masked
        out.write(data, 1)
    report = validate_output(designed["dir"] / "final.tif", designed["nir"], designed["red"], summary, samples=200)
    assert any("outside [-1, 1]" in failure for failure in report["failures"])
    assert report["strip_mismatches"] >= 1


def test_the_inputs_are_not_modified_by_a_masked_run(designed):
    before = {k: sha(designed[k].path) for k in ("red", "nir")}
    make(designed, "final.tif", (-1.0, 1.0))
    assert before == {k: sha(designed[k].path) for k in ("red", "nir")}
