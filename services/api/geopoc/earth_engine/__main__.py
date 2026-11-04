"""Owner-run command line for the GEE-1 controlled evaluation. Prints JSON; never prints a credential.

    python -m geopoc.earth_engine init          create the control folder (GEOPOC_EARTH_ENGINE_CONTROL_DIR)
    python -m geopoc.earth_engine status        local state only; nothing is sent
    python -m geopoc.earth_engine datasets      the read-only catalogue
    python -m geopoc.earth_engine verify        the connection check (sends no area of interest)
    python -m geopoc.earth_engine probe SLUG    one dataset's availability (sends no area of interest)
    python -m geopoc.earth_engine kill [WHY]    engage the global kill switch
    python -m geopoc.earth_engine resume        deliberately turn Earth Engine back on
    python -m geopoc.earth_engine verify-log    check the usage log's chain

`verify` and `probe` take the single-instance lock, so they refuse while the API is using Earth Engine (and the other
way round): stop the API, or use its endpoints instead. See docs/earth-engine-setup-and-evaluation.md.
"""

import argparse
import getpass
import json
import sys
from typing import Any

from geopoc.config import get_settings
from geopoc.earth_engine import catalogue, killswitch, verify
from geopoc.earth_engine.control import ControlPaths
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.earth_engine.runtime import get_runtime, reset_runtimes
from geopoc.earth_engine.usage import verify_file


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _actor() -> str:
    return f"cli:{getpass.getuser()}"[:80]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m geopoc.earth_engine", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "datasets", "verify", "resume", "verify-log"):
        sub.add_parser(name)
    sub.add_parser("probe").add_argument("slug")
    sub.add_parser("kill").add_argument("reason", nargs="?", default="engaged from the command line")
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        paths = ControlPaths.from_settings(settings)
        if args.command == "init":
            paths.create()
            _print({"ok": True, "control_folder": str(paths.directory), "note": "Created if it was missing."})
        elif args.command == "kill":
            killswitch.engage(paths, _actor(), args.reason)
            _print({"ok": True, "kill_switch": "engaged"})
        elif args.command == "resume":
            removed = killswitch.resume(paths)
            _print({"ok": True, "kill_switch": "off", "flag_removed": removed})
        elif args.command == "verify-log":
            result = verify_file(paths)
            _print(result)
            return 0 if result["ok"] else 1
        elif args.command == "status":
            runtime = get_runtime(settings)
            state = killswitch.state(paths)
            _print(
                {
                    "kill_switch_engaged": state.engaged,
                    "kill_switch_reason": state.reason or None,
                    "usage": runtime.snapshot().get("log"),
                    "online_enabled": settings.online_enabled,
                    "earth_engine_enabled": settings.earth_engine_enabled,
                    "project_configured": bool(settings.earth_engine_project),
                    "auth_mode": settings.earth_engine_auth_mode,
                }
            )
        elif args.command == "datasets":
            _print(catalogue.listing(get_runtime(settings).probes))
        elif args.command == "verify":
            report = verify.run_connection_test(get_runtime(settings), _actor(), require_developer=False)
            _print(report)
            return 0 if report["ok"] else 1
        elif args.command == "probe":
            result = catalogue.probe(get_runtime(settings), args.slug, _actor(), require_developer=False)
            _print(result.as_dict())
            return 0 if result.status == "available" else 1
    except EarthEngineRefused as exc:
        _print({"ok": False, "refused": exc.code, "message": str(exc)})
        return 2
    finally:
        reset_runtimes()
    return 0


if __name__ == "__main__":
    sys.exit(main())
