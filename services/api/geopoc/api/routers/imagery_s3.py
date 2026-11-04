"""CDSE S3 authentication check, Phase 1 of the S3 integration: report the state, and verify the credentials on request.

`GET /imagery/s3/status` reads configuration only and never contacts the store. `POST /imagery/s3/verify` makes one
signed, read-only listing request (at most one key) and only after an explicit confirmation. Neither sends a project or
an area of interest, downloads anything, or writes to the database or to the store. Nothing here returns a credential.
"""

from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends

from geopoc.api.deps import ProfileDep
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.imagery import HooksDep, SettingsDep
from geopoc.api.schemas import CdseS3StatusOut, CdseS3VerifyOut, CdseS3VerifyRequest
from geopoc.imagery import cdse_s3
from geopoc.imagery.cdse_s3 import CdseS3Settings, S3Status

router = APIRouter(prefix="/api/v1", tags=["imagery"], responses=problem_responses(422))


def get_s3_settings() -> CdseS3Settings:
    """The CDSE_S3_* settings. A separate dependency so a test can replace them for these routes only."""
    return cdse_s3.get_cdse_s3_settings()


S3SettingsDep = Annotated[CdseS3Settings, Depends(get_s3_settings)]


def _out(result: S3Status) -> dict:
    return {
        "enabled": result.enabled,
        "configured": result.configured,
        "authenticated": result.authenticated,
        "status": result.status,
        "endpoint": result.endpoint,
        "region": result.region,
        "reason": result.reason,
        "message": result.message,
        "online_gate_open": result.online_gate_open,
        "checked_at": result.checked_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if result.checked_at
        else None,
    }


@router.get("/imagery/s3/status", response_model=CdseS3StatusOut, responses=problem_responses(401))
def s3_status(s3: S3SettingsDep, settings: SettingsDep, profile: ProfileDep):
    """The state of the CDSE S3 configuration. Reads configuration only: it makes no request and checks no credential,
    so `authenticated` is always false here. Use the verification to check them."""
    return _out(cdse_s3.describe(s3, online_enabled=settings.online_enabled))


@router.post(
    "/imagery/s3/verify",
    response_model=CdseS3VerifyOut,
    responses=problem_responses(400, 401, 409, 422),
)
def s3_verify(
    body: CdseS3VerifyRequest, s3: S3SettingsDep, settings: SettingsDep, hooks: HooksDep, profile: ProfileDep
):
    """Check the credentials with one signed, read-only listing request (at most one key) to the S3 endpoint.

    Refused with 409 unless `confirm_external_request` is true. When the integration is off, the online gate is
    closed or the credentials are missing, the answer says so and nothing is sent, whatever the confirmation."""
    online = settings.online_enabled
    request = cdse_s3.request_summary(s3)
    if cdse_s3.describe(s3, online_enabled=online).status == "NOT_VERIFIED" and not body.confirm_external_request:
        raise ProblemError(
            409,
            "CDSE_S3_CONFIRMATION_REQUIRED",
            "Confirm before contacting the S3 endpoint",
            "Verification sends one signed, read-only listing request to the S3 endpoint. It includes your access "
            "key id (never the secret key) and no project or area data. Repeat with confirm_external_request true. "
            f"Request: {request['method']} {request['url']}",
        )
    result = cdse_s3.verify(s3, online_enabled=online, transport=hooks.transport)
    return {**_out(result), "request": request}
