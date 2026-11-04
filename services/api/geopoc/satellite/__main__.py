"""Command line for the feasibility prototype. Offline by default: the provider is `mock` unless asked otherwise.

    python -m geopoc.satellite plan   --bbox W S E N --scene ID [--bands B4 B3 B2] [--scale 10]
    python -m geopoc.satellite search --bbox W S E N --start 2026-01-01 --end 2026-01-31 [--provider mock|earthengine]
    python -m geopoc.satellite export --bbox W S E N --scene ID --acquired 2026-01-06T05:30:00Z --scratch <folder>

`--provider earthengine` is refused unless the account owner has switched on GEOPOC_ONLINE_ENABLED and
GEOPOC_EARTH_ENGINE_ENABLED and the other prerequisites exist (see docs/earth-engine-feasibility.md). Nothing is ever
printed that looks like a credential.
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime

from geopoc.config import get_settings
from geopoc.satellite.earthengine import EarthEngineProvider
from geopoc.satellite.export import ExportRefused, ScratchDir, run_export
from geopoc.satellite.gate import ProviderError, redact
from geopoc.satellite.mock import MockProvider
from geopoc.satellite.models import (
    COLLECTION_ID,
    DEFAULT_BANDS,
    AreaOfInterest,
    DateRange,
    RequestRefused,
    SceneInfo,
    SceneQuery,
    plan_export,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m geopoc.satellite", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "search", "export"):
        p = sub.add_parser(name)
        p.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"), required=True)
        p.add_argument("--provider", choices=("mock", "earthengine"), default="mock")
        if name == "search":
            p.add_argument("--start", type=date.fromisoformat, required=True)
            p.add_argument("--end", type=date.fromisoformat, required=True)
            p.add_argument("--max-cloud", type=float)
        else:
            p.add_argument("--scene", required=True)
            p.add_argument("--bands", nargs="+", default=list(DEFAULT_BANDS))
            p.add_argument("--scale", type=int, default=10)
        if name == "export":
            p.add_argument(
                "--acquired",
                type=datetime.fromisoformat,
                required=True,
                help="acquisition time from the search, e.g. 2026-01-06T05:30:00Z",
            )
            p.add_argument("--cloud", type=float, help="cloud percent from the search, if known")
            p.add_argument("--tile")
            p.add_argument("--scratch", required=True, help="a folder whose name starts with _satellite")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        aoi = AreaOfInterest.from_bbox(*args.bbox)
        if args.command == "plan":
            print(
                json.dumps(
                    plan_export(args.scene, aoi, bands=tuple(args.bands), scale_m=args.scale).as_dict(), indent=2
                )
            )
            return 0
        provider = MockProvider() if args.provider == "mock" else EarthEngineProvider.from_settings(get_settings())
        if args.command == "search":
            query = SceneQuery(aoi, DateRange(args.start, args.end), args.max_cloud)
            print(json.dumps([s.as_dict() for s in provider.search(query)], indent=2))
            return 0
        plan = plan_export(args.scene, aoi, bands=tuple(args.bands), scale_m=args.scale)
        if args.acquired.tzinfo is None:
            raise RequestRefused(
                "ACQUIRED_NO_ZONE", "Give the acquisition time with a UTC offset, as the search printed it"
            )
        scene = SceneInfo(
            scene_id=args.scene,
            collection=COLLECTION_ID,
            acquisition_time=args.acquired.astimezone(UTC),
            cloud_percent=args.cloud,
            tile=args.tile,
        )
        result = run_export(provider, plan, scene, ScratchDir.at(args.scratch))
        print(json.dumps({"tif": str(result.tif_path), "manifest": str(result.manifest_path)}, indent=2))
        return 0
    except (RequestRefused, ExportRefused) as exc:
        print(f"refused ({exc.code}): {exc}", file=sys.stderr)
    except ProviderError as exc:
        print(f"{type(exc).__name__}: {redact(exc)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
