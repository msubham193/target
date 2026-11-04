"""The minimal connection-verification workflow: can the organisation-owned identity initialise Earth Engine for the
designated project, and may it run a computation? Sends no area of interest and no geometry. Manual only: nothing calls
it on a timer.

A failed check is a normal, reported result (`ok: false` with the stable code and the next step); a refusal before
anything was sent (disabled, kill switch, ceilings, another instance) is raised as `EarthEngineRefused`.
"""

from datetime import UTC, datetime
from typing import Any

from geopoc.earth_engine import client
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.earth_engine.runtime import Runtime

COST = client.COST_CONNECT + client.COST_PING


def run_connection_test(
    runtime: Runtime,
    actor: str,
    *,
    request_id: str | None = None,
    ee_module: Any | None = None,
    auth=None,
    require_developer: bool = True,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    report: dict[str, Any] = {
        "ok": False,
        "checked_at": started.isoformat(),
        "auth_mode": runtime.settings.earth_engine_auth_mode,
        "credential_kind": None,
        "service_account_email": None,
        "project_configured": bool(runtime.settings.earth_engine_project),
        "initialised": False,
        "computation_ok": False,
        "library_version": None,
        "failure": None,
    }
    runtime.connection = None  # always verify from scratch
    with runtime.operation(
        "connection_test", COST, actor, request_id=request_id, require_developer=require_developer
    ) as op:
        try:
            ee = ee_module or client.load_ee()
            connection = client.connect(runtime, op, ee_module=ee, auth=auth)
            report.update(
                credential_kind=connection.credential_kind,
                service_account_email=connection.service_account_email,
                library_version=connection.library_version,
                initialised=True,
            )
            client.ping(op, ee)
            report["computation_ok"] = True
            report["ok"] = True
        except EarthEngineRefused as exc:
            if exc.code in ("EE_KILL_SWITCH", "EE_PACKAGE_MISSING", "EE_NOT_CONFIGURED"):
                # Nothing (or nothing more) was sent: a refusal, not a result.
                if not report["initialised"]:
                    op.release(exc.code)
                raise
            report["failure"] = {"code": exc.code, "message": str(exc)}
        op.outcome = "ok" if report["ok"] else "failed"
        op.detail = "ok" if report["ok"] else str((report["failure"] or {}).get("code"))
    runtime.last_verification = report
    runtime.usage.note("verification", ok=report["ok"], mode=report["auth_mode"], kind=report["credential_kind"] or "")
    return report
