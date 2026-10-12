"""PT-01: every documented operation is exercised with generated requests and checked against the OpenAPI document."""

import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_schema_conformance,
    status_code_conformance,
)
from sqlalchemy import text

from geopoc.api.main import app
from geopoc.db.session import get_engine

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
