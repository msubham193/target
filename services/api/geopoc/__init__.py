import os

__version__ = "0.1.0"

# GDAL must never read or write .aux.xml (PAM) files next to a raster on its own: sidecar metadata is reviewed and
# confirmed by a person (geopoc.geo.sidecars), and nothing is ever written beside an original. Set before any module
# opens a raster; job processes import this package too, so they inherit it.
os.environ["GDAL_PAM_ENABLED"] = "NO"

# PROJ must never fetch grids or definitions, for pyproj or for GDAL. An environment that asks for it is overridden,
# unconditionally and before any raster is opened (GDAL reads this when it first creates a PROJ context). The original
# request is kept so the health report can say it was ignored. See geopoc.geo.projenv.
PROJ_NETWORK_REQUESTED = os.environ.get("PROJ_NETWORK")
os.environ["PROJ_NETWORK"] = "OFF"

# pyproj reads the variable when it is imported, so a program that imported it first already has the network on in
# pyproj's own switch. Turn that off here too, not only later in geopoc.geo.crs / geopoc.geo.transform.
import pyproj  # noqa: E402 - must come after the variable is set

pyproj.network.set_network_enabled(False)
