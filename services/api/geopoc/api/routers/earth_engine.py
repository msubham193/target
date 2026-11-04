"""Earth Engine, GEE-1: status, the read-only dataset catalogue, and the manual connection check.

Operating model A (docs/earth-engine-integration-assessment.md, section 16.3a): named developers only, fixed low
ceilings, a global kill switch, fail closed. These routes send no area of interest, serve no tile, export nothing and
keep no imagery. They read the database only to identify the local profile (attribution, not authentication) and write
nothing to it; usage is recorded in the append-only local log outside PostgreSQL.
"""

import importlib.util
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path

from geopoc.api.deps import ProfileDep, RequestIdDep
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    ConnectionTest,
    DatasetEntry,
    DatasetList,
    DatasetProbe,
    EarthEngineStatus,
)
from geopoc.config import Settings, get_settings
from geopoc.earth_engine import catalogue, killswitch, verify
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.earth_engine.guard import guard_for
from geopoc.earth_engine.runtime import developers, get_runtime

router = APIRouter(prefix="/api/v1/earth-engine", tags=["earth-engine"], responses=problem_responses(422))

TITLES = {
    "EE_DISABLED": "Earth Engine is switched off",
    "EE_NOT_CONFIGURED": "Earth Engine is not configured",
    "EE_KILL_SWITCH": "Earth Engine is stopped by the kill switch",
    "EE_CONTROL_UNAVAILABLE": "The Earth Engine control folder is not available",
    "EE_LOG_UNAVAILABLE": "The Earth Engine usage log is not available",
    "EE_PACKAGE_MISSING": "The Earth Engine client is not installed",
    "EE_DEVELOPER_ONLY": "Earth Engine is limited to named developers",
    "EE_INSTANCE_CONFLICT": "Another process is using Earth Engine",
    "EE_BUDGET_EXCEEDED": "An Earth Engine usage ceiling was reached",
    "EE_AUTH_FAILED": "Earth Engine authentication failed",
    "EE_UPSTREAM": "Earth Engine reported an error",
    "EE_UNKNOWN_DATASET": "Unknown dataset",
}
REFUSALS = problem_responses(401, 403, 404, 409, 429, 502)


@dataclass
class EeHooks:
    """Stand-ins for the Earth Engine library and Google auth. None in production; tests override the dependency."""

    ee_module: Any = None
    auth: Any = None


def get_hooks() -> EeHooks:
    return EeHooks()


def get_ee_settings() -> Settings:
    """The application settings. A separate dependency so a test can replace them for these routes only."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_ee_settings)]
HooksDep = Annotated[EeHooks, Depends(get_hooks)]


def _problem(exc: EarthEngineRefused) -> ProblemError:
    return ProblemError(exc.status, exc.code, TITLES.get(exc.code, "Earth Engine was not used"), str(exc))


# --- routes -----------------------------------------------------------------------------------------------------------


@router.get("/status", response_model=EarthEngineStatus, responses=problem_responses(401))
def get_status(settings: SettingsDep, profile: ProfileDep):
    """Local state only: nothing is sent to Google and no usage is charged. Shows no credential."""
    control_ready, problem, usage, held, runtime = False, None, None, False, None
    try:
        runtime = get_runtime(settings)
        runtime.paths.check_ready()
        control_ready = True
        usage = runtime.snapshot().get("log")
        held = guard_for(runtime.paths.lock_file).held
    except EarthEngineRefused as exc:
        problem = str(exc)
    kill = killswitch.state(runtime.paths) if runtime is not None else killswitch.KillState(True, problem or "")
    names = developers(settings)
    return EarthEngineStatus(
        operating_model="A",
        online_enabled=settings.online_enabled,
        earth_engine_enabled=settings.earth_engine_enabled,
        project_configured=bool(settings.earth_engine_project),
        auth_mode=settings.earth_engine_auth_mode,
        client_installed=importlib.util.find_spec("ee") is not None,
        control_folder_ready=control_ready,
        control_folder_problem=problem,
        kill_switch_engaged=kill.engaged,
        kill_switch_reason=kill.reason or None,
        held_by_this_process=held,
        usage=usage,
        developers_listed=len(names),
        you_are_listed=profile.display_name in names,
        last_verification=runtime.last_verification if runtime is not None else None,
        live_verified=bool(runtime is not None and (runtime.last_verification or {}).get("ok")),
        note="Operating model A: named developers, one host, one process, fixed low ceilings, a global kill switch. "
        "No imagery, tile, export or area of interest is involved in this phase.",
    )


@router.get("/datasets", response_model=DatasetList, responses=problem_responses(401))
def list_datasets(settings: SettingsDep, profile: ProfileDep):
    """The catalogue and each dataset's latest probe result. Read-only; never contacts Google."""
    try:
        probes = get_runtime(settings).probes
    except EarthEngineRefused:
        probes = {}  # not configured: the catalogue is still shown, with every probe 'not_probed'
    return DatasetList(
        items=[DatasetEntry(**item) for item in catalogue.listing(probes)],
        note="Facts were read on the providers' dataset pages on the date shown (verified_on). A dataset is confirmed "
        "available to this project only after a developer runs its probe (status 'available').",
    )


@router.post("/connection-test", response_model=ConnectionTest, responses=REFUSALS)
def connection_test(settings: SettingsDep, hooks: HooksDep, profile: ProfileDep, request_id: RequestIdDep):
    """Manual: initialise Earth Engine as the organisation-owned identity for the designated project and run one trivial
    computation. Sends no area of interest. A failed check is a normal 200 result with `ok: false` and the reason."""
    try:
        runtime = get_runtime(settings)
        return verify.run_connection_test(
            runtime, profile.display_name, request_id=request_id, ee_module=hooks.ee_module, auth=hooks.auth
        )
    except EarthEngineRefused as exc:
        raise _problem(exc) from None


@router.post("/datasets/{slug}/probe", response_model=DatasetProbe, responses=REFUSALS)
def probe_dataset(
    slug: Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")],
    settings: SettingsDep,
    hooks: HooksDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
):
    """Manual: ask the service for one catalogue dataset's time extent. Sends no area of interest or geometry."""
    try:
        catalogue.get(slug)
        runtime = get_runtime(settings)
        result = catalogue.probe(
            runtime, slug, profile.display_name, request_id=request_id, ee_module=hooks.ee_module, auth=hooks.auth
        )
    except EarthEngineRefused as exc:
        raise _problem(exc) from None
    return result.as_dict()
