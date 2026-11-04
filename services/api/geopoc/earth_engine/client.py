"""Organisation-owned authentication and the connection to Earth Engine.

The identity must be owned by the organisation: an attached service account, a workload-identity-federated one, an
impersonated one, or (last resort, only with the explicit allow switch) a service-account key file. An individual
person's Google credentials are refused. No credential is ever read into settings, logged, printed or returned; only the
*kind* of credential and the service account's e-mail address (not a secret) are reported.

`ee` and `google.auth` are imported only here, only when an operation has been reserved, and can be replaced by
stand-ins in tests. Nothing in this module has been exercised against the live service (no organisation credentials
exist on this host); see docs/earth-engine-setup-and-evaluation.md.
"""

import importlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from geopoc.config import REPO_ROOT, Settings
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.earth_engine.runtime import Operation
from geopoc.satellite.gate import redact

EE_SCOPE = "https://www.googleapis.com/auth/earthengine"

#: Requests each step is charged against the usage ceilings (a credential refresh counts as one).
COST_CONNECT = 2
COST_PING = 1


class GoogleAuth(Protocol):
    def default(self, scopes: list[str]) -> Any: ...
    def from_key_file(self, path: str, scopes: list[str]) -> Any: ...


class _RealGoogleAuth:
    def default(self, scopes: list[str]) -> Any:
        import google.auth

        credentials, _project = google.auth.default(scopes=scopes)
        return credentials

    def from_key_file(self, path: str, scopes: list[str]) -> Any:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(path, scopes=scopes)


def load_ee() -> Any:
    try:
        return importlib.import_module("ee")
    except ImportError:
        raise EarthEngineRefused(
            "EE_PACKAGE_MISSING",
            "The earthengine-api package is not installed. Install it with `uv sync --inexact --group earthengine`.",
        ) from None


def credential_kind(credentials: Any) -> str:
    """A label for what was found, from the class only. Never looks inside the credential."""
    module = type(credentials).__module__
    name = type(credentials).__name__
    if module.startswith("google.oauth2.service_account"):
        return "service_account_key"
    if "impersonated" in module:
        return "impersonated_service_account"
    if "identity_pool" in module or "external_account" in module or module.endswith(".aws") or "pluggable" in module:
        return "external_account"
    if module.startswith("google.auth.compute_engine"):
        return "attached_service_account"
    if module.startswith("google.oauth2.credentials") or name == "UserAccessTokenCredentials":
        return "user"
    return "other"


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def _check_key_file(settings: Settings) -> str:
    if not settings.earth_engine_allow_key_file:
        raise EarthEngineRefused(
            "EE_NOT_CONFIGURED",
            "A service-account key file is the last resort and is not allowed: set GEOPOC_EARTH_ENGINE_ALLOW_KEY_FILE "
            "only "
            "under a written administrator exception. Prefer an attached, federated or impersonated identity.",
        )
    configured = settings.earth_engine_credentials_file
    if configured is None:
        raise EarthEngineRefused("EE_NOT_CONFIGURED", "GEOPOC_EARTH_ENGINE_CREDENTIALS_FILE is not set.")
    path = Path(configured)
    if not path.is_file():
        raise EarthEngineRefused("EE_NOT_CONFIGURED", "The credentials file named in settings does not exist.")
    if _inside(path, REPO_ROOT) or _inside(path, settings.data_root):
        raise EarthEngineRefused(
            "EE_NOT_CONFIGURED",
            "The credentials file is inside the repository or the data folder. Keep it elsewhere, readable only by the "
            "service user.",
        )
    if sys.platform != "win32" and path.stat().st_mode & 0o077:
        raise EarthEngineRefused("EE_NOT_CONFIGURED", "The credentials file is readable by other users; restrict it.")
    return str(path)


def build_credentials(settings: Settings, auth: GoogleAuth | None = None) -> tuple[Any, str]:
    """Credentials for the organisation-owned identity, and a label for their kind. Refuses anything else."""
    auth = auth or _RealGoogleAuth()
    mode = settings.earth_engine_auth_mode
    scopes = [EE_SCOPE]
    try:
        if mode == "key_file":
            credentials = auth.from_key_file(_check_key_file(settings), scopes)
        else:
            if mode == "wif":
                variable = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
                if not variable or not Path(variable).is_file():
                    raise EarthEngineRefused(
                        "EE_NOT_CONFIGURED",
                        "auth mode wif needs GOOGLE_APPLICATION_CREDENTIALS to name a workload identity federation "
                        "configuration file.",
                    )
                try:
                    declared = json.loads(Path(variable).read_text(encoding="utf-8")).get("type")
                except (OSError, ValueError, AttributeError):
                    declared = None
                if declared != "external_account":
                    raise EarthEngineRefused(
                        "EE_NOT_CONFIGURED",
                        "auth mode wif, but GOOGLE_APPLICATION_CREDENTIALS is not an external_account configuration.",
                    )
            credentials = auth.default(scopes)
    except EarthEngineRefused:
        raise
    except Exception as exc:  # the Google libraries' messages can echo what they were given
        raise EarthEngineRefused(
            "EE_AUTH_FAILED",
            redact(
                f"No usable credentials for auth mode {mode}: {type(exc).__name__}. Check Application Default "
                f"Credentials (docs/earth-engine-setup-and-evaluation.md)."
            ),
        ) from None

    kind = credential_kind(credentials)
    if kind == "user":
        raise EarthEngineRefused(
            "EE_AUTH_FAILED",
            "The credentials found belong to an individual person's Google account. GEE-1 requires an "
            "organisation-owned "
            "identity (attached, federated or impersonated service account).",
        )
    if kind == "service_account_key" and mode != "key_file":
        raise EarthEngineRefused(
            "EE_AUTH_FAILED",
            f"auth mode {mode} resolved to a long-lived service-account key. Keys are not permitted unless auth mode "
            f"is "
            "key_file and GEOPOC_EARTH_ENGINE_ALLOW_KEY_FILE is set under an administrator exception.",
        )
    if mode == "impersonation" and kind != "impersonated_service_account":
        raise EarthEngineRefused(
            "EE_AUTH_FAILED", f"auth mode impersonation, but the credentials found are of kind {kind}."
        )
    if mode == "wif" and kind != "external_account":
        raise EarthEngineRefused("EE_AUTH_FAILED", f"auth mode wif, but the credentials found are of kind {kind}.")
    return credentials, kind


@dataclass
class Connection:
    project: str
    credential_kind: str
    service_account_email: str | None
    library_version: str
    initialised_in_ms: int


def _classify(exc: Exception) -> EarthEngineRefused:
    """Turn a library failure into a refusal with the next step, without echoing anything credential-like."""
    text = redact(f"{type(exc).__name__}: {exc}")[:300]
    low = text.lower()
    if "serviceusage" in low or "service usage" in low or "serviceusageconsumer" in low:
        hint = "The identity needs roles/serviceusage.serviceUsageConsumer on the project."
        return EarthEngineRefused("EE_AUTH_FAILED", f"{hint} ({text})")
    if "not registered" in low or "not signed up" in low or "earth engine api has not been used" in low:
        hint = "The Cloud project is not registered for Earth Engine, or the Earth Engine API is not enabled on it."
        return EarthEngineRefused("EE_AUTH_FAILED", f"{hint} ({text})")
    if "permission" in low or "403" in low or "denied" in low or "forbidden" in low:
        return EarthEngineRefused(
            "EE_AUTH_FAILED",
            f"Permission denied. The identity needs earthengine.computations.create (roles/earthengine.writer or a "
            f"custom role). ({text})",
        )
    if "quota" in low or "429" in low or "too many" in low or "rate limit" in low:
        return EarthEngineRefused("EE_UPSTREAM", f"Earth Engine reported a quota or rate limit. ({text})")
    return EarthEngineRefused("EE_UPSTREAM", f"Earth Engine reported an error. ({text})")


def connect(runtime, op: Operation, *, ee_module: Any | None = None, auth: GoogleAuth | None = None) -> Connection:
    """Build the organisation-owned credentials and initialise Earth Engine for the designated project.

    The kill switch is checked before each step. Costs COST_CONNECT requests; the caller reserved them."""
    settings = runtime.settings
    ee = ee_module or load_ee()
    op.checkpoint()
    started = time.monotonic()
    credentials, kind = build_credentials(settings, auth)
    op.checkpoint()
    try:
        ee.Initialize(credentials=credentials, project=settings.earth_engine_project)
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from None
    connection = Connection(
        project=str(settings.earth_engine_project),
        credential_kind=kind,
        service_account_email=getattr(credentials, "service_account_email", None),
        library_version=str(getattr(ee, "__version__", "unknown")),
        initialised_in_ms=int((time.monotonic() - started) * 1000),
    )
    runtime.connection = connection
    return connection


def ping(op: Operation, ee: Any) -> None:
    """One trivial server-side computation: proves the identity may run computations, not just authenticate."""
    op.checkpoint()
    try:
        answer = ee.Number(1).add(1).getInfo()
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from None
    if answer != 2:
        raise EarthEngineRefused("EE_UPSTREAM", "Earth Engine answered a trivial computation incorrectly; not trusted.")
