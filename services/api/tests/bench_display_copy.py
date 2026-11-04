"""Benchmark (not a test): tile rendering from a large striped raster vs its verified display copy.

    uv run python -m tests.bench_display_copy [output_folder]

Writes a synthetic 3-band uint8 GeoTIFF (no overviews, striped, uncompressed) to the output folder (default
D:\\geopoc-cache\\bench), builds and verifies a display copy, and times the same zoomed-out and zoomed-in tiles from
both. Synthetic data; timings depend on this machine and its disk cache.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pyproj
import rasterio
from rasterio.transform import from_origin

from geopoc.geo import displaycopy, tiles

SIZE = 12000  # pixels per side: 432 MB uncompressed for three uint8 bands


def write_raster(path: Path) -> None:
    profile = {
        "driver": "GTiff",
        "width": SIZE,
        "height": SIZE,
        "count": 3,
        "dtype": "uint8",
        "crs": "EPSG:32643",
        "transform": from_origin(500000, 3000000, 1, 1),
        "tiled": False,
    }
    rows = 500
    with rasterio.open(path, "w", **profile) as dst:
        for row in range(0, SIZE, rows):
            r, c = np.mgrid[row : row + rows, 0:SIZE]
            block = np.stack([((r + c * (b + 1)) % 251).astype("uint8") for b in range(3)])
            dst.write(block, window=rasterio.windows.Window(0, row, SIZE, rows))


def tile_for(lon: float, lat: float, z: int) -> tuple[int, int]:
    x_m, y_m = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(lon, lat)
    size = 2 * tiles.ORIGIN / 2**z
    return int((x_m + tiles.ORIGIN) / size), int((tiles.ORIGIN - y_m) / size)


def timed(path: Path, z: int, x: int, y: int, repeats: int = 3) -> float:
    best = float("inf")
    for _ in range(repeats):
        started = time.perf_counter()
        tiles.render_tile(path, (1, 2, 3), (None, None, None), z, x, y)
        best = min(best, time.perf_counter() - started)
    return round(best * 1000, 1)


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\geopoc-cache\bench")
    folder.mkdir(parents=True, exist_ok=True)
    original, copy = folder / "large_striped.tif", folder / "large_display.tif"
    started = time.perf_counter()
    write_raster(original)
    written_s = time.perf_counter() - started
    build = displaycopy.build(original, copy)
    lon, lat = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform(506000, 2994000)
    results = {"raster": f"{SIZE} x {SIZE} x 3 uint8, striped, no overviews", "write_s": round(written_s, 1)}
    results["display_copy"] = {
        "build_s": build["build_seconds"],
        "size_mb": round(build["size_bytes"] / 1024**2, 1),
        "original_mb": round(original.stat().st_size / 1024**2, 1),
        "overview_levels": build["verification"]["overview_levels"],
        "verified_windows": build["verification"]["windows_compared"],
    }
    results["tile_ms_best_of_3"] = {}
    for z in (12, 14, 17):  # the whole raster in about one tile; a quarter of it; full resolution
        x, y = tile_for(lon, lat, z)
        results["tile_ms_best_of_3"][f"z{z}"] = {
            "original": timed(original, z, x, y),
            "display_copy": timed(copy, z, x, y),
        }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
