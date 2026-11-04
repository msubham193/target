"""PT-01: every documented operation is exercised with generated requests and checked against the OpenAPI document."""

import pytest
from sqlalchemy import text

from geopoc.api.main import app
from geopoc.db.session import get_engine

# Schemathesis needs a native library (jsonschema_rs). Windows Smart App Control sometimes blocks it on the development
# machine; this test is then SKIPPED, loudly and with the reason, never silently. The pure-Python contract tests in
# test_openapi_contract.py and test_openapi_drift.py cover the same ground and always run. (When the block also stops
# pytest from starting, scripts\pytest.ps1 disables only the Schemathesis plugin for the run.)
try:
    import schemathesis
    from hypothesis import HealthCheck, settings
    from schemathesis.checks import not_a_server_error
    from schemathesis.specs.openapi.checks import (
        content_type_conformance,
        response_schema_conformance,
        status_code_conformance,
    )
except (ImportError, OSError) as exc:
    pytest.skip(
        f"Schemathesis cannot be imported on this machine ({type(exc).__name__}: {str(exc)[:160]}). Windows Smart App "
        "Control blocks its native library. The pure-Python contract tests (test_openapi_contract.py, "
        "test_openapi_drift.py) still run.",
        allow_module_level=True,
    )

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

PROFILE_ID = "00000000-0000-4000-8000-000000000001"


def ensure_profile() -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace_user (id, display_name, created_at) "
                "VALUES (:id, 'Contract Test', now()) ON CONFLICT DO NOTHING"
            ),
            {"id": PROFILE_ID},
        )


@schema.parametrize()
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_api_conforms_to_openapi(case):
    ensure_profile()
    case.call_and_validate(
        headers={"X-Workspace-Profile": PROFILE_ID},
        checks=[
            not_a_server_error,
            status_code_conformance,
            content_type_conformance,
            response_schema_conformance,
        ],
    )
