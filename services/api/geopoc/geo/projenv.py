"""The PROJ environment the application runs in, and whether its two copies of PROJ agree.

Two separate copies of PROJ are in play: the one inside pyproj (planning, footprints, point conversion) and the one
bundled with rasterio's GDAL (drawing tiles, warping). They are different builds with their own `proj.db`. This module:

* reports that PROJ network access is off for both. `geopoc/__init__.py` forces `PROJ_NETWORK=OFF` when the package is
  imported, before any raster is opened, so a request in the environment to go online cannot fetch anything;
* reports what each copy is (versions, data directories, EPSG database versions) and what could change that;
* runs a fixed-coordinate comparison of the two engines, because "the versions differ" says nothing about whether the
  answers do.

What it cannot do, and says so: rasterio exposes GDAL's *configuration value* for `PROJ_NETWORK`, which is verified, but
not the state PROJ holds inside GDAL, which is not directly observable with the installed API. A report never claims
more than that.

Differences between the two EPSG databases are a warning, not a refusal: they are real but small, and the parity
check is what decides whether positions agree. A parity failure is a failure and is shown as one.
"""

import logging
import os
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

import pyproj
from pyproj import Transformer

log = logging.getLogger(__name__)

# 1 mm. The two engines run the same published projection maths, so for datum-free pairs they should agree far
# better than this; anything larger means they are not using the same definitions.
PARITY_TOLERANCE_M = 0.001
_METRES_PER_DEGREE = (
    111_320.0  # an upper bound on a degree of longitude or latitude, so a degree error is never understated
)

# Datum-free pairs only: GDAL chooses its own operation, so a datum shift would compare two choices, not two engines.
PARITY_CASES: tuple[tuple[str, str, float, float], ...] = (
    ("EPSG:32643", "EPSG:4326", 500000.0, 3000000.0),
    ("EPSG:4326", "EPSG:3857", 77.1, 28.6),
    ("EPSG:3857", "EPSG:4326", 8590000.0, 3350000.0),
    ("EPSG:32660", "EPSG:4326", 300000.0, 5000000.0),
    ("EPSG:2193", "EPSG:4326", 1748000.0, 5428000.0),  # northing first in its definition
    ("EPSG:2046", "EPSG:4326", 50000.0, -3750000.0),  # westing / southing
    ("EPSG:3031", "EPSG:4326", 100000.0, -200000.0),  # polar, grid directions
)


def _proj_db(data_dir: Path | None) -> dict[str, Any]:
    info: dict[str, Any] = {"data_dir": str(data_dir) if data_dir else None}
    if data_dir is None:
        return info
    db = data_dir / "proj.db"
    try:
        with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as con:
            meta = dict(con.execute("SELECT key, value FROM metadata"))
        info.update(
            epsg_version=meta.get("EPSG.VERSION"),
            epsg_date=meta.get("EPSG.DATE"),
            proj_data_version=meta.get("PROJ_DATA.VERSION"),
            layout=f"{meta.get('DATABASE.LAYOUT.VERSION.MAJOR')}.{meta.get('DATABASE.LAYOUT.VERSION.MINOR')}",
            database_proj_version=meta.get("PROJ.VERSION"),
        )
    except (sqlite3.Error, OSError) as exc:
        info["error"] = f"proj.db could not be read: {exc}"
    ini = data_dir / "proj.ini"
    try:
        lines = [ln.strip() for ln in ini.read_text(errors="ignore").splitlines() if ln.strip().startswith("network")]
        info["ini_network"] = lines[0] if lines else None
    except OSError:
        info["ini_network"] = None
    return info


def _gdal_data_dir() -> Path | None:
    try:
        import rasterio
        from rasterio.env import PROJDataFinder

        found = PROJDataFinder().search()
        if found:
            return Path(found)
        bundled = Path(rasterio.__file__).parent / "proj_data"
        return bundled if bundled.exists() else None
    except Exception:  # noqa: BLE001 - reported as "could not be located", never raised into startup
        return None


def _major_minor(version: str | None) -> tuple[str, str] | None:
    parts = (version or "").split(".")
    return (parts[0], parts[1]) if len(parts) >= 2 else None


def _to_metres(crs: str, delta_x: float, delta_y: float) -> float:
    target = pyproj.CRS.from_user_input(crs)
    if target.is_geographic:
        return max(delta_x, delta_y) * _METRES_PER_DEGREE
    factor = target.axis_info[0].unit_conversion_factor or 1.0
    return max(delta_x, delta_y) * factor


def parity() -> dict[str, Any]:
    """The same coordinates through pyproj's PROJ and GDAL's PROJ. `failed` if any pair differs by more than 1 mm or
    cannot be converted by one of them."""
    import rasterio.warp

    cases: list[dict[str, Any]] = []
    worst = 0.0
    status = "ok"
    for source, target, x, y in PARITY_CASES:
        case: dict[str, Any] = {"source": source, "target": target}
        try:
            px, py = Transformer.from_crs(source, target, always_xy=True).transform(x, y)
            gx, gy = rasterio.warp.transform(source, target, [x], [y])
            error = _to_metres(target, abs(px - gx[0]), abs(py - gy[0]))
            case["max_difference_m"] = error
            worst = max(worst, error)
            if not error <= PARITY_TOLERANCE_M:  # also catches NaN
                status = "failed"
        except Exception as exc:  # noqa: BLE001 - one engine failing is itself the finding
            case["error"] = f"{type(exc).__name__}: {exc}"
            status = "failed"
        cases.append(case)
    return {"status": status, "tolerance_m": PARITY_TOLERANCE_M, "worst_difference_m": worst, "cases": cases}


@lru_cache(maxsize=1)
def report() -> dict[str, Any]:
    """The full report. Computed once per process: the environment does not change while the application runs."""
    import rasterio
    import rasterio.env

    import geopoc

    requested = getattr(geopoc, "PROJ_NETWORK_REQUESTED", None)
    checks: list[dict[str, str]] = []

    def check(code: str, status: str, message: str) -> None:
        checks.append({"code": code, "status": status, "message": message})

    # --- network ---
    if requested is not None and requested.strip().upper() not in ("", "OFF", "FALSE", "0"):
        check(
            "PROJ_NETWORK_REQUEST_IGNORED",
            "warning",
            f"The environment asked for PROJ_NETWORK={requested}. It was overridden to OFF: this application never "
            "fetches grids or definitions.",
        )
    if pyproj.network.is_network_enabled():
        check("PYPROJ_NETWORK_OFF", "failed", "pyproj PROJ network access is enabled.")
    else:
        check("PYPROJ_NETWORK_OFF", "ok", "pyproj PROJ network access is disabled.")
    try:
        gdal_value = rasterio.env.get_gdal_config("PROJ_NETWORK")
    except Exception:  # noqa: BLE001
        gdal_value = None
    if gdal_value is None:
        check(
            "GDAL_NETWORK_CONFIG",
            "warning",
            "GDAL's PROJ_NETWORK configuration could not be read, so it is not verified.",
        )
    elif gdal_value:
        check("GDAL_NETWORK_CONFIG", "failed", "GDAL's PROJ_NETWORK configuration is enabled.")
    else:
        check(
            "GDAL_NETWORK_CONFIG",
            "ok",
            "GDAL's PROJ_NETWORK configuration is OFF. This is the configuration value; the state PROJ holds inside "
            "GDAL cannot be observed directly with the installed API, so it is not independently verified.",
        )

    # --- data directories and overrides ---
    overrides = {name: os.environ.get(name) for name in ("PROJ_DATA", "PROJ_LIB") if os.environ.get(name)}
    if overrides:
        check(
            "PROJ_DATA_OVERRIDE",
            "warning",
            f"{', '.join(overrides)} is set in the environment. Both PROJ copies may be reading that one database "
            "instead of their own, which this report cannot distinguish from the bundled ones.",
        )
    py_dir = Path(pyproj.datadir.get_data_dir())
    py = {"proj_version": pyproj.proj_version_str, "pyproj_version": pyproj.__version__, **_proj_db(py_dir)}
    gdal = {
        "rasterio_version": rasterio.__version__,
        "gdal_version": rasterio.__gdal_version__,
        "proj_version": rasterio.__proj_version__,
        **_proj_db(_gdal_data_dir()),
    }

    # --- versions ---
    if _major_minor(py["proj_version"]) != _major_minor(gdal["proj_version"]):
        check(
            "PROJ_VERSION",
            "failed",
            f"pyproj uses PROJ {py['proj_version']} and GDAL uses PROJ {gdal['proj_version']}: different major or "
            "minor versions.",
        )
    elif py["proj_version"] != gdal["proj_version"]:
        check(
            "PROJ_VERSION",
            "ok",
            f"pyproj uses PROJ {py['proj_version']} and GDAL uses PROJ {gdal['proj_version']}: the same release line, "
            "different patch versions.",
        )
    else:
        check("PROJ_VERSION", "ok", f"Both use PROJ {py['proj_version']}.")
    if py.get("layout") and gdal.get("layout") and py["layout"] != gdal["layout"]:
        check("DATABASE_LAYOUT", "failed", f"proj.db layouts differ: {py['layout']} vs {gdal['layout']}.")
    if py.get("epsg_version") and gdal.get("epsg_version") and py["epsg_version"] != gdal["epsg_version"]:
        check(
            "EPSG_DATABASE_VERSION",
            "warning",
            f"The EPSG databases differ: pyproj has {py['epsg_version']} ({py.get('epsg_date')}), GDAL has "
            f"{gdal['epsg_version']} ({gdal.get('epsg_date')}). Operations or accuracy figures edited between those "
            "releases could be seen differently by the two engines. The parity check below decides whether positions "
            "agree; it does not cover every CRS.",
        )

    # --- do the two engines give the same answer? ---
    try:
        result = parity()
    except Exception as exc:  # noqa: BLE001
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "cases": []}
    if result["status"] == "failed":
        check(
            "PARITY",
            "failed",
            "pyproj and GDAL gave different positions (or one failed) for fixed datum-free test coordinates. Maps "
            "and footprints may disagree. This is a failure, not a version warning.",
        )
    else:
        check(
            "PARITY",
            "ok",
            f"pyproj and GDAL agree within {PARITY_TOLERANCE_M * 1000:g} mm on {len(result['cases'])} fixed "
            f"coordinate pairs (worst {result['worst_difference_m'] * 1000:.6f} mm).",
        )

    statuses = {c["status"] for c in checks}
    overall = "failed" if "failed" in statuses else "warning" if "warning" in statuses else "ok"
    if overall != "ok":
        log.warning("PROJ environment: %s %s", overall, [c for c in checks if c["status"] != "ok"])
    return {
        "status": overall,
        "network": {
            "proj_network_env": os.environ.get("PROJ_NETWORK"),
            "requested_before_override": requested,
            "pyproj_enabled": pyproj.network.is_network_enabled(),
            "gdal_configuration_enabled": gdal_value,
            "gdal_internal_state_verified": False,
        },
        "pyproj": py,
        "gdal": gdal,
        "overrides": overrides,
        "parity": result,
        "checks": checks,
    }
