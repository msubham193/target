"""Development entry point: `uv run python -m geopoc`. Serves on loopback only."""

import uvicorn

from geopoc.config import LOOPBACK_HOSTS, get_settings


def check_loopback(host: str) -> None:
    if host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"Refusing to bind to {host!r}: this application is local-only and has no production-grade "
            "authentication. Use 127.0.0.1."
        )


def main() -> None:
    settings = get_settings()
    check_loopback(settings.api_host)
    uvicorn.run("geopoc.api.main:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
