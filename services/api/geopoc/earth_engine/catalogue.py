"""A read-only catalogue of the Earth Engine datasets in scope for release 1, and the availability probe.

The static entries hold only what was read on the data providers' and Google's dataset pages (see `verified_on` and
`source_url` on each). Dates are the documented *start* dates only: the pages disagree with themselves about end dates,
so an end date is never hard-coded; the probe reads the collection's real extent from the service.

Nothing here is a claim about what the project's identity may read. That is what the probe is for, and no probe has been
run against the live service on this host (no organisation credentials exist here).

The probe sends no area of interest and no geometry: it asks one question about the whole collection's time extent.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from geopoc.earth_engine import client
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.earth_engine.runtime import Runtime
from geopoc.satellite.gate import redact

VERIFIED_ON = "2026-10-31"
CATALOG = "https://developers.google.com/earth-engine/datasets/catalog/"


@dataclass(frozen=True)
class Band:
    name: str
    description: str
    pixel_size_m: int | None = None
    scale: float | None = None
    offset: float | None = None
    units: str | None = None


@dataclass(frozen=True)
class Dataset:
    slug: str
    id: str
    title: str
    kind: str  # optical | sar | cloud_mask
    provider: str
    revisit_days: int | None
    documented_start: str  # the documented first date (ISO); an end date is deliberately not recorded
    bands: tuple[Band, ...]
    licence: str
    attribution: str
    cloud_handling: str
    notes: tuple[str, ...] = ()
    verified_on: str = VERIFIED_ON
    source_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


S2_BANDS = tuple(
    Band(name, desc, size, 0.0001)
    for name, desc, size in (
        ("B1", "Aerosols", 60),
        ("B2", "Blue", 10),
        ("B3", "Green", 10),
        ("B4", "Red", 10),
        ("B5", "Red edge 1", 20),
        ("B6", "Red edge 2", 20),
        ("B7", "Red edge 3", 20),
        ("B8", "Near infrared", 10),
        ("B8A", "Red edge 4", 20),
        ("B9", "Water vapour", 60),
        ("B11", "Shortwave infrared 1", 20),
        ("B12", "Shortwave infrared 2", 20),
    )
) + (
    Band("SCL", "Scene classification map (values 1 to 11)", 20),
    Band("QA60", "Cloud mask; masked out from 2022-01-25 to 2024-02-28 inclusive", 60),
)

LANDSAT_SR = tuple(
    Band(f"SR_B{n}", name, 30, 0.0000275, -0.2)
    for n, name in (
        (1, "Ultra blue / coastal aerosol"),
        (2, "Blue"),
        (3, "Green"),
        (4, "Red"),
        (5, "Near infrared"),
        (6, "Shortwave infrared 1"),
        (7, "Shortwave infrared 2"),
    )
) + (
    Band("ST_B10", "Surface temperature", 30, 0.00341802, 149.0, "K"),
    Band("QA_PIXEL", "Pixel quality: fill, dilated cloud, cirrus, cloud, cloud shadow, snow, clear, water", 30),
    Band("QA_RADSAT", "Radiometric saturation and terrain occlusion", 30),
)

DATASETS: tuple[Dataset, ...] = (
    Dataset(
        slug="sentinel-2-sr-harmonized",
        id="COPERNICUS/S2_SR_HARMONIZED",
        title="Harmonized Sentinel-2 MSI, Level-2A (surface reflectance)",
        kind="optical",
        provider="European Union / ESA / Copernicus",
        revisit_days=5,
        documented_start="2017-03-28",
        bands=S2_BANDS,
        licence="Copernicus Sentinel Data Terms and Conditions",
        attribution="Contains modified Copernicus Sentinel data.",
        cloud_handling="CLOUDY_PIXEL_PERCENTAGE is the granule-level share of cloudy pixels, not the share over an "
        "area. "
        "Pixel masks: Cloud Score+ (preferred) or the cloud-probability collection. QA60 is not consistent over time.",
        notes=(
            "Band values are stored as unsigned integers; multiply by the scale (0.0001) for reflectance.",
            "Global coverage in 2017 and 2018 is incomplete.",
        ),
        source_url=CATALOG + "COPERNICUS_S2_SR_HARMONIZED",
    ),
    Dataset(
        slug="sentinel-2-cloud-probability",
        id="COPERNICUS/S2_CLOUD_PROBABILITY",
        title="Sentinel-2 cloud probability",
        kind="cloud_mask",
        provider="European Union / ESA / Copernicus / SentinelHub",
        revisit_days=5,
        documented_start="2015-06-27",
        bands=(Band("probability", "Probability that the pixel is cloudy (0 to 100)", 10),),
        licence="Copernicus Sentinel Data Terms and Conditions (see the catalogue page)",
        attribution="Contains modified Copernicus Sentinel data.",
        cloud_handling="Joined to the optical collection on system:index; the catalogue example masks at 65 or above.",
        source_url=CATALOG + "COPERNICUS_S2_CLOUD_PROBABILITY",
    ),
    Dataset(
        slug="cloud-score-plus-s2",
        id="GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED",
        title="Cloud Score+ S2_HARMONIZED V1",
        kind="cloud_mask",
        provider="Google",
        revisit_days=5,
        documented_start="2015-06-27",
        bands=(
            Band("cs", "Pixel quality score from spectral distance to a theoretical clear reference (0 to 1)", 10),
            Band("cs_cdf", "Likelihood the pixel is clear, from how its score ranks over time (0 to 1)", 10),
        ),
        licence="CC-BY-4.0",
        attribution="Cloud Score+ (Google), CC-BY-4.0.",
        cloud_handling="Linked to the optical collection by system:index. The catalogue says thresholds from 0.50 to "
        "0.65 "
        "generally work well; its example uses cs_cdf at 0.60.",
        notes=("The catalogue said the backfill of the whole Sentinel-2 archive was still in progress.",),
        source_url=CATALOG + "GOOGLE_CLOUD_SCORE_PLUS_V1_S2_HARMONIZED",
    ),
    Dataset(
        slug="sentinel-1-grd",
        id="COPERNICUS/S1_GRD",
        title="Sentinel-1 SAR GRD: C-band synthetic aperture radar, ground range detected, log scaling",
        kind="sar",
        provider="European Union / ESA / Copernicus",
        revisit_days=6,
        documented_start="2014-10-03",
        bands=(
            Band("HH", "Single co-polarisation, horizontal transmit and receive", 10, units="dB"),
            Band("HV", "Dual-band cross-polarisation, horizontal transmit, vertical receive", 10, units="dB"),
            Band("VV", "Single co-polarisation, vertical transmit and receive", 10, units="dB"),
            Band("VH", "Dual-band cross-polarisation, vertical transmit, horizontal receive", 10, units="dB"),
            Band("angle", "Approximate incidence angle from the ellipsoid, degrees", None, units="degrees"),
        ),
        licence="Copernicus Sentinel Data Terms and Conditions",
        attribution="Contains modified Copernicus Sentinel data.",
        cloud_handling="Radar sees through cloud; no cloud mask is needed. Filter on instrumentMode and "
        "transmitterReceiverPolarisation (and orbit pass) before combining scenes.",
        notes=(
            "Preprocessing: thermal noise removal, radiometric calibration, terrain correction (SRTM 30 m; ASTER "
            "above 60 "
            "degrees latitude), then conversion to decibels. Raw power is in COPERNICUS/S1_GRD_FLOAT.",
        ),
        source_url=CATALOG + "COPERNICUS_S1_GRD",
    ),
    Dataset(
        slug="landsat-8-c2-l2",
        id="LANDSAT/LC08/C02/T1_L2",
        title="USGS Landsat 8 Level 2, Collection 2, Tier 1",
        kind="optical",
        provider="USGS / NASA",
        revisit_days=16,
        documented_start="2013-03-18",
        bands=LANDSAT_SR,
        licence="Public domain; USGS credit requested",
        attribution="Landsat 8 image courtesy of the U.S. Geological Survey.",
        cloud_handling="QA_PIXEL bits: 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow, 5 snow, 6 clear, 7 "
        "water; "
        "confidence in bits 8 to 15.",
        notes=("Apply the scale and offset together (the offset means the scale alone is wrong).",),
        source_url=CATALOG + "LANDSAT_LC08_C02_T1_L2",
    ),
    Dataset(
        slug="landsat-9-c2-l2",
        id="LANDSAT/LC09/C02/T1_L2",
        title="USGS Landsat 9 Level 2, Collection 2, Tier 1",
        kind="optical",
        provider="USGS / NASA",
        revisit_days=16,
        documented_start="2021-10-31",
        bands=LANDSAT_SR,
        licence="Public domain; USGS credit requested",
        attribution="Landsat 9 image courtesy of the U.S. Geological Survey.",
        cloud_handling="As Landsat 8: QA_PIXEL bits.",
        notes=(
            "PROCESSING_LEVEL is L2SP when surface reflectance and surface temperature are both present, L2SR when "
            "only "
            "reflectance is; the temperature bands are then present but fully masked.",
        ),
        source_url=CATALOG + "LANDSAT_LC09_C02_T1_L2",
    ),
)

_BY_SLUG = {d.slug: d for d in DATASETS}
_BY_ID = {d.id: d for d in DATASETS}


def get(slug_or_id: str) -> Dataset:
    dataset = _BY_SLUG.get(slug_or_id) or _BY_ID.get(slug_or_id)
    if dataset is None:
        raise EarthEngineRefused("EE_UNKNOWN_DATASET", "That dataset is not in the catalogue.")
    return dataset


# --- the availability probe ------------------------------------------------------------------------------------------

STATUSES = ("not_probed", "available", "not_found", "permission_denied", "quota", "error")
COST_PROBE = 1


@dataclass
class ProbeResult:
    slug: str
    dataset_id: str
    status: str = "not_probed"
    checked_at: str | None = None
    extent_start: str | None = None
    extent_end: str | None = None
    detail: str = ""
    live: bool = field(default=False, metadata={"doc": "True only for a result obtained from the service"})

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso(millis: Any) -> str | None:
    if not isinstance(millis, int | float):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _status_for(exc: Exception) -> tuple[str, str]:
    text = redact(f"{type(exc).__name__}: {exc}")[:240]
    low = text.lower()
    if "not found" in low or "does not exist" in low or "no such" in low:
        return "not_found", text
    if "permission" in low or "403" in low or "denied" in low or "forbidden" in low:
        return "permission_denied", text
    if "quota" in low or "429" in low or "too many" in low or "rate limit" in low:
        return "quota", text
    return "error", text


def listing(probes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The catalogue with the latest probe result for each dataset. Local only: no call to Google, no usage charged."""
    out = []
    for dataset in DATASETS:
        probe = probes.get(dataset.slug) or ProbeResult(dataset.slug, dataset.id).as_dict()
        out.append({**dataset.as_dict(), "probe": probe})
    return out


def probe(
    runtime: Runtime,
    slug: str,
    actor: str,
    *,
    request_id: str | None = None,
    ee_module: Any | None = None,
    auth=None,
    require_developer: bool = True,
) -> ProbeResult:
    """Ask the service for one collection's time extent. Sends no geometry. Charged against the usage ceilings."""
    dataset = get(slug)
    cost = COST_PROBE + (0 if runtime.connection is not None else client.COST_CONNECT)
    with runtime.operation(
        "dataset_probe", cost, actor, request_id=request_id, require_developer=require_developer
    ) as op:
        ee = ee_module or client.load_ee()
        if runtime.connection is None:
            client.connect(runtime, op, ee_module=ee, auth=auth)
        result = ProbeResult(dataset.slug, dataset.id, checked_at=datetime.now(UTC).isoformat(), live=True)
        op.checkpoint()
        try:
            collection = ee.ImageCollection(dataset.id)
            info = ee.Dictionary(
                {
                    "start": collection.aggregate_min("system:time_start"),
                    "end": collection.aggregate_max("system:time_start"),
                }
            ).getInfo()
        except Exception as exc:  # noqa: BLE001 - classified, never echoed raw
            result.status, result.detail = _status_for(exc)
        else:
            start, end = _iso((info or {}).get("start")), _iso((info or {}).get("end"))
            if start is None:
                result.status, result.detail = "error", "The collection answered but reported no image dates."
            else:
                result.status, result.extent_start, result.extent_end = "available", start, end
                if start[:10] > dataset.documented_start:
                    result.detail = (
                        f"Real extent starts {start[:10]}; the documentation says {dataset.documented_start}."
                    )
        op.outcome = "ok" if result.status == "available" else "failed"
        op.detail = f"{dataset.slug}:{result.status}"
    runtime.probes[dataset.slug] = result.as_dict()
    runtime.usage.note("probe", dataset=dataset.slug, status=result.status)
    return result
