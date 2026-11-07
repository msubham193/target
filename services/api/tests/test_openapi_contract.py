"""API contract tests in pure Python: no Schemathesis, no native extension, no new dependency.

They check the application against its own OpenAPI document (PT-01), and work where Windows Smart App Control blocks
the Schemathesis plugin:

1. The document promises a complete error contract for every operation (static).
2. Real error responses keep that promise: every operation is probed with unknown ids, malformed ids, a missing profile, wrong
   bodies and hostile values; each answer must be a documented status, in the documented media type, and (for errors) a valid
   `Problem` body whose `status` and `request_id` match the response. No input may cause a 5xx.
3. Successful responses are validated against the Pydantic response models, and every operation must be either exercised or
   listed in NOT_EXERCISED with a reason, so a new route cannot slip in unchecked.

Everything runs against the scratch database `geopoc_test` through the in-process application (see conftest.py).
"""

import csv
import importlib
import io
import json
import re
import uuid
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter

from geopoc.api.errors import Problem
from geopoc.api.main import app
from tests import media
from tests.test_ground_pipeline import STUB, analyze, photo

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
PROBLEM = "application/problem+json"
UNKNOWN_UUID = "00000000-0000-4000-8000-00000000dead"
NASTY = [
    "",
    " ",
    "a" * 3000,
    "'; DROP TABLE asset; --",
    "../../../etc/passwd",
    "%",
    "%00",
    "日本語",
    "NaN",
    "-1",
    "0",
    "99999999999999999999",
    "1e400",
    "null",
    "{}",
    "<script>alert(1)</script>",
]

SPEC = app.openapi()


def all_operations() -> list[tuple[str, str, dict]]:
    return [
        (method.upper(), path, op)
        for path, item in SPEC["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS
    ]


OPERATIONS = all_operations()
IDS = [f"{method} {path}" for method, path, _ in OPERATIONS]


# --- request building from the document -------------------------------------------------------------------------------


def schema_of(parameter: dict) -> dict:
    schema = parameter.get("schema", {})
    if "anyOf" in schema:  # an optional parameter: take the non-null alternative
        schema = next((s for s in schema["anyOf"] if s.get("type") != "null"), schema["anyOf"][0])
    return schema


def sample(parameter: dict):
    schema = schema_of(parameter)
    if "enum" in schema:
        return schema["enum"][0]
    if schema.get("format") == "uuid":
        return UNKNOWN_UUID
    kind = schema.get("type")
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return "true"
    return "x"


def malformed(parameter: dict):
    schema = schema_of(parameter)
    if schema.get("format") == "uuid":
        return "not-a-uuid"
    if schema.get("type") in ("integer", "number"):
        return "not-a-number"
    return None  # a string cannot be malformed


def profile_header(op: dict) -> str | None:
    for parameter in op.get("parameters", []):
        if parameter["in"] == "header" and parameter["name"].lower() == "x-workspace-profile":
            return parameter["name"]
    return None


def json_body_required(op: dict) -> bool:
    body = op.get("requestBody")
    return bool(body and "application/json" in body.get("content", {}))


def build(op: dict, path: str, *, values: dict | None = None, profile: str | None, with_profile=True, body="default"):
    """(url, query params, headers, json body) for one operation. `values` overrides individual path/query values."""
    values = values or {}
    url = path
    query: dict = {}
    for parameter in op.get("parameters", []):
        name, where = parameter["name"], parameter["in"]
        if where == "header":
            continue
        value = values.get(name, sample(parameter))
        if where == "path":
            url = url.replace("{" + name + "}", quote(str(value), safe=""))
        elif where == "query" and (parameter.get("required") or name in values):
            query[name] = value
    headers = {}
    header = profile_header(op)
    if header and with_profile and profile:
        headers[header] = profile
    json_body = {} if body == "default" and json_body_required(op) else (None if body == "default" else body)
    return url, query, headers, json_body


def send(client: TestClient, method: str, url: str, query: dict, headers: dict, json_body):
    kwargs = {"params": query, "headers": headers}
    if json_body is not None:
        kwargs["json"] = json_body
    return client.request(method, url, **kwargs)


# --- what a response must satisfy ---------------------------------------------------------------------------------------


def media_type(response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip()


def assert_conforms(method: str, path: str, op: dict, response, label: str) -> None:
    where = f"{method} {path} [{label}] -> HTTP {response.status_code}"
    assert response.status_code < 500, f"{where}: a server error. Body: {response.text[:300]}"
    documented = op["responses"]
    status = str(response.status_code)
    assert status in documented, (
        f"{where}: this status is not documented (documented: {sorted(documented)}). Body: {response.text[:200]}"
    )
    content = documented[status].get("content")
    if content:
        assert media_type(response) in content, (
            f"{where}: media type {media_type(response)!r} is not among the documented {sorted(content)}"
        )
    if media_type(response) == PROBLEM:
        problem = Problem.model_validate(response.json())
        assert problem.status == response.status_code, (
            f"{where}: body status {problem.status} differs from the HTTP status"
        )
        assert problem.code and problem.title, f"{where}: a problem needs a code and a title"
        assert problem.request_id == response.headers.get("x-request-id"), (
            f"{where}: request_id differs from X-Request-ID"
        )


@pytest.fixture
def lenient():
    """The application in-process, returning 5xx responses instead of re-raising, so they can be reported as failures."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --- 1. the document itself --------------------------------------------------------------------------------------------


def without_null_defaults(value):
    """FastAPI leaves `"default": null` out of the published document; compare the meaning, not that detail."""
    if isinstance(value, dict):
        return {k: without_null_defaults(v) for k, v in value.items() if not (k == "default" and v is None)}
    if isinstance(value, list):
        return [without_null_defaults(v) for v in value]
    return value


def test_the_document_has_operations_and_documents_the_problem_model_as_it_is():
    assert len(OPERATIONS) >= 50
    documented = SPEC["paths"]["/api/v1/projects"]["get"]["responses"]["422"]["content"][PROBLEM]["schema"]
    assert without_null_defaults(documented) == without_null_defaults(Problem.model_json_schema()), (
        "the documented error body differs from the Problem model"
    )


@pytest.mark.parametrize(("method", "path", "op"), OPERATIONS, ids=IDS)
def test_every_operation_declares_a_complete_error_contract(method, path, op):
    responses = op["responses"]
    assert any(code.startswith("2") for code in responses), "no success response is documented"
    has_input = any(p["in"] in ("path", "query") for p in op.get("parameters", [])) or "requestBody" in op
    if has_input:
        assert "422" in responses, "an operation with input must document 422"
    if profile_header(op):
        assert "401" in responses, "an operation that needs a profile must document 401"
    if any(p["in"] == "path" and schema_of(p).get("format") == "uuid" for p in op.get("parameters", [])):
        assert "404" in responses, "an operation addressing a record by id must document 404"
    for code, response in responses.items():
        if code.startswith("2") or "content" not in response:
            continue
        if path == "/health" and code == "503":
            continue  # the health document explains an unavailable service with its own body
        assert list(response["content"]) == [PROBLEM], f"{code} must use {PROBLEM}, found {list(response['content'])}"
        schema = response["content"][PROBLEM]["schema"]
        assert schema.get("title") == "Problem" or schema.get("$ref", "").endswith("/Problem"), (
            f"{code} does not use the Problem schema"
        )


# --- 2. real errors keep the promise -----------------------------------------------------------------------------------


@pytest.fixture
def real_profile(lenient):
    response = lenient.post("/api/v1/workspace/profiles", json={"display_name": "Contract probe"})
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize(("method", "path", "op"), OPERATIONS, ids=IDS)
def test_unknown_malformed_and_unauthenticated_requests_get_documented_problem_answers(
    lenient, real_profile, method, path, op
):
    parameters = [p for p in op.get("parameters", []) if p["in"] == "path"]
    has_body = json_body_required(op)

    # unknown (well-formed) ids, valid profile
    url, query, headers, body = build(op, path, profile=real_profile)
    response = send(lenient, method, url, query, headers, body)
    assert_conforms(method, path, op, response, "unknown ids")
    if any(schema_of(p).get("format") == "uuid" for p in parameters) and not has_body and "404" in op["responses"]:
        assert response.status_code == 404, f"{method} {path}: an unknown id should be 404, got {response.status_code}"

    # malformed ids -> 422
    bad = {p["name"]: malformed(p) for p in parameters if malformed(p) is not None}
    if bad:
        url, query, headers, body = build(op, path, values=bad, profile=real_profile)
        response = send(lenient, method, url, query, headers, body)
        assert_conforms(method, path, op, response, "malformed path value")
        assert response.status_code == 422, f"{method} {path}: a malformed id should be 422, got {response.status_code}"

    # no profile -> 401, wherever one is required
    if profile_header(op):
        url, query, headers, body = build(op, path, profile=real_profile, with_profile=False)
        response = send(lenient, method, url, query, headers, body)
        assert_conforms(method, path, op, response, "no profile")
        assert response.status_code == 401, (
            f"{method} {path}: a missing profile should be 401, got {response.status_code}"
        )
        assert response.json()["code"] == "PROFILE_REQUIRED"

    # a body that is not JSON at all -> a documented 400 (found by Schemathesis: this was undocumented)
    if has_body:
        url, query, headers, _ = build(op, path, profile=real_profile)
        headers = {**headers, "content-type": "application/json"}
        for broken in (b"{not json", b"\x00\x01\x02", b'{"a": ', b"\xff\xfe\xfd", b"W5\xef\xbf\xbd;dm8\xff\x00'"):
            response = lenient.request(method, url, params=query, headers=headers, content=broken)
            assert_conforms(method, path, op, response, f"unparsable body {broken[:10]!r}")
            assert response.status_code in (400, 422), (
                f"{method} {path}: unparsable JSON gave HTTP {response.status_code}"
            )

    # wrong bodies -> 422
    if has_body:
        for label, wrong in (
            ("array body", []),
            ("number body", 7),
            ("unexpected fields", {"__unexpected__": 1}),
            ("empty object", {}),
        ):
            url, query, headers, _ = build(op, path, profile=real_profile)
            response = send(lenient, method, url, query, headers, wrong)
            assert_conforms(method, path, op, response, label)
            if label in ("array body", "number body"):
                assert response.status_code == 422, (
                    f"{method} {path}: {label} should be 422, got {response.status_code}"
                )


@pytest.mark.parametrize(("method", "path", "op"), OPERATIONS, ids=IDS)
def test_hostile_values_never_cause_a_server_error(lenient, real_profile, method, path, op):
    inputs = [p for p in op.get("parameters", []) if p["in"] in ("path", "query")]
    for parameter in inputs:
        for value in NASTY:
            if parameter["in"] == "path" and value == "":
                continue  # an empty path segment is a different URL (the collection route), not a hostile value
            url, query, headers, body = build(op, path, values={parameter["name"]: value}, profile=real_profile)
            response = send(lenient, method, url, query, headers, body)
            assert_conforms(method, path, op, response, f"{parameter['name']}={value[:30]!r}")


def test_the_checks_can_fail(lenient):
    """A response that breaks the contract must be reported: an undocumented status and a wrong media type."""
    _, _, op = next(o for o in OPERATIONS if o[0] == "GET" and o[1] == "/api/v1/projects")
    response = lenient.get("/api/v1/projects")
    assert_conforms("GET", "/api/v1/projects", op, response, "control")
    fake = type("R", (), {"status_code": 418, "headers": {}, "text": "teapot", "json": lambda self: {}})()
    with pytest.raises(AssertionError, match="not documented"):
        assert_conforms("GET", "/api/v1/projects", op, fake, "forced")
    wrong_media = type(
        "R", (), {"status_code": 200, "headers": {"content-type": "text/plain"}, "text": "x", "json": lambda self: {}}
    )()
    with pytest.raises(AssertionError, match="media type"):
        assert_conforms("GET", "/api/v1/projects", op, wrong_media, "forced")


# --- 3. successful responses ---------------------------------------------------------------------------------------------

# Operations not exercised here, each with the reason. Behavioural coverage is in the named test module. A new operation
# that is neither exercised below nor listed here fails test_every_operation_is_exercised_or_explained.
NOT_EXERCISED = {
    (
        "POST",
        "/api/v1/models/custom",
    ): "opens and test-runs an uploaded model in a child process; covered in test_custom_models.py",
    (
        "DELETE",
        "/api/v1/models/custom/{name}/{version}",
    ): "needs an uploaded model first; covered in test_custom_models.py",
    (
        "POST",
        "/api/v1/projects/{project_id}/report/summary",
    ): "calls a local language model; a schema fuzz must not drive it. Covered with a fake model in test_report_summary.py",
    (
        "GET",
        "/api/v1/report-summary/status",
    ): "asks the local Ollama server; covered with a fake in test_report_summary.py",
    (
        "POST",
        "/api/v1/assets/{asset_id}/scene-description",
    ): "calls a local vision model; a schema fuzz must not drive it. Covered with a fake model in test_scene_description.py",
    (
        "POST",
        "/api/v1/assets/{asset_id}/georeferencing",
    ): "records a version; covered end to end in test_georeferencing.py",
    (
        "POST",
        "/api/v1/assets/{asset_id}/display-copy",
    ): "queues a job for large rasters only; covered in test_raster_tools.py",
    (
        "POST",
        "/api/v1/projects/{project_id}/comparisons",
    ): "needs two aligned rasters and a job run; covered in test_raster_tools.py",
    ("GET", "/api/v1/projects/{project_id}/comparisons"): "lists comparisons; covered in test_raster_tools.py",
    ("GET", "/api/v1/assets/{asset_id}/alignment/{other_id}"): "needs two rasters; covered in test_raster_tools.py",
    (
        "POST",
        "/api/v1/assets/{asset_id}/control-points",
    ): "records a version; covered end to end in test_control_points.py",
    (
        "GET",
        "/api/v1/projects/{project_id}/alignment",
    ): "needs two placed rasters; covered in test_alignment.py",
    ("POST", "/api/v1/projects/{project_id}/sidecars"): "multi-file upload flow; covered in test_sidecars.py",
    ("POST", "/api/v1/assets/{asset_id}/sidecars"): "multi-file upload flow; covered in test_sidecars.py",
    ("POST", "/api/v1/assets/{asset_id}/sidecars/validate"): "needs uploaded sidecars; covered in test_sidecars.py",
    ("POST", "/api/v1/assets/{asset_id}/sidecars/apply"): "records a version; covered in test_sidecars.py",
    (
        "GET",
        "/api/v1/projects/{project_id}/assets/{asset_id}/video-analysis/plan",
    ): "needs a video (written by ffmpeg); covered in test_video_frames.py",
    (
        "GET",
        "/api/v1/projects/{project_id}/assets/{asset_id}/video-analysis/runs/{run_id}/frames",
    ): "needs a finished video analysis run; covered in test_video_frames.py",
    (
        "GET",
        "/api/v1/projects/{project_id}/assets/{asset_id}/video-analysis/runs/{run_id}/frames/{frame_index}/image",
    ): "needs a finished video analysis run; covered in test_video_frames.py",
    (
        "GET",
        "/api/v1/projects/{project_id}/assets/{asset_id}/terrain",
    ): "needs an elevation raster (and refuses imagery); covered in test_terrain.py",
    (
        "GET",
        "/api/v1/projects/{project_id}/assets/{asset_id}/terrain/grid",
    ): "needs an elevation raster (and refuses imagery); covered in test_terrain.py",
    (
        "POST",
        "/api/v1/earth-engine/connection-test",
    ): "needs a configured Earth Engine and stand-ins for the client; covered in test_earth_engine.py",
    (
        "POST",
        "/api/v1/earth-engine/datasets/{slug}/probe",
    ): "needs a configured Earth Engine and stand-ins for the client; covered in test_earth_engine.py",
    ("POST", "/api/v1/jobs/{job_id}/start"): "needs a draft job; covered in test_jobs.py",
    ("POST", "/api/v1/jobs/{job_id}/retry"): "needs a failed job; covered in test_jobs.py",
}


def _models() -> dict[str, type[BaseModel]]:
    """Every Pydantic model the API can name in its document, by class name (the name used in `$ref`)."""
    found: dict[str, type[BaseModel]] = {}
    for module_name in (
        "geopoc.api.schemas",
        "geopoc.contracts.observation",
        "geopoc.contracts.review",
        "geopoc.api.errors",
        "geopoc.reporting",
    ):
        for value in vars(importlib.import_module(module_name)).values():
            if isinstance(value, type) and issubclass(value, BaseModel) and value is not BaseModel:
                found.setdefault(value.__name__, value)
    return found


MODELS = _models()


def model_for(schema: dict):
    """The Pydantic type a documented JSON response schema refers to, or None for free-form objects."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        assert name in MODELS, f"the document names response model {name!r} but no such Pydantic class was found"
        return MODELS[name]
    if schema.get("type") == "array" and "items" in schema:
        inner = model_for(schema["items"])
        return list[inner] if inner is not None else None
    return None


class Scenario:
    """Records which operations a scenario exercised, and validates each response against its model."""

    def __init__(self, client: TestClient, profile_id: str, headers: dict):
        self.client, self.profile_id, self.headers = client, profile_id, headers
        self.exercised: set[tuple[str, str]] = set()

    def call(self, method: str, template: str, expect: int, *, path=None, headers=True, **kwargs):
        url = template
        for name, value in (path or {}).items():
            url = url.replace("{" + name + "}", quote(str(value), safe=""))
        assert "{" not in url, f"unfilled path parameter in {url}"
        response = self.client.request(method, url, headers=self.headers if headers else {}, **kwargs)
        assert response.status_code == expect, (
            f"{method} {url}: expected {expect}, got {response.status_code}: {response.text[:300]}"
        )
        op = SPEC["paths"][template][method.lower()]
        assert str(expect) in op["responses"], f"{method} {template}: {expect} is not documented"
        content = op["responses"][str(expect)].get("content")
        if content:
            assert media_type(response) in content, (
                f"{method} {template}: media type {media_type(response)!r} not in {sorted(content)}"
            )
        if media_type(response) == "application/json":
            # The wire JSON must satisfy the model the document names. (FastAPI checks routes that declare a response
            # model; this also covers routes such as /health that build their JSON by hand.)
            model = model_for(content["application/json"].get("schema", {}))  # a file download documents no schema
            if model is not None:
                TypeAdapter(model).validate_python(response.json())
        self.exercised.add((method, template))
        return response


def test_successful_responses_conform_and_every_operation_is_accounted_for(client, profile, headers, project, run_jobs):
    pid = project["id"]
    s = Scenario(client, profile["id"], headers)
    P = {"project_id": pid}

    # --- reference data and the workspace ---
    s.call("GET", "/health", 200)
    s.call("GET", "/health/models", 200)
    s.call("GET", "/api/v1/ingestion/rules", 200)
    s.call("GET", "/api/v1/crs", 200, params={"q": "WGS 84", "limit": 3})
    s.call(
        "GET", "/api/v1/crs/transform", 200, params={"source": "EPSG:4326", "target": "EPSG:3857", "x": 75.0, "y": 27.0}
    )
    operations = s.call(
        "GET", "/api/v1/crs/operations", 200, params={"source": "EPSG:4326", "target": "EPSG:3857"}
    ).json()
    assert operations["plan"]["status"] == "exact"
    s.call("GET", "/api/v1/workspace/profiles", 200)

    # --- Earth Engine (GEE-1): the two local, read-only routes; they send nothing to Google ---
    s.call("GET", "/api/v1/earth-engine/status", 200)
    s.call("GET", "/api/v1/earth-engine/datasets", 200)

    # --- Catalogue discovery (Phase 1): the providers list is local; a dry run sends nothing; a real search is
    # refused while the gates are closed (their defaults), so no request leaves this machine ---
    s.call("GET", "/api/v1/imagery/providers", 200)
    area = {
        "type": "Polygon",
        "coordinates": [[[10.0, 50.0], [10.05, 50.0], [10.05, 50.05], [10.0, 50.05], [10.0, 50.0]]],
    }
    search = {"aoi": area, "start_date": "2024-05-20", "end_date": "2024-06-20", "max_cloud_percent": 40}
    dry = s.call("POST", "/api/v1/projects/{project_id}/scene-search", 200, path=P, json={**search, "dry_run": True})
    assert dry.json()["transmission"]["sent"] is False
    refused = s.call(
        "POST",
        "/api/v1/projects/{project_id}/scene-search",
        409,
        path=P,
        json={**search, "confirm_external_transmission": True},
    )
    assert refused.json()["code"] == "IMAGERY_DISABLED"

    # --- CDSE S3 authentication check: the status is local; verification is refused without a confirmation, and with
    # one it still sends nothing here because the integration is off (the test environment pins it off) ---
    # --- Derived spectral indices: read-only listing of the indices and of the locally stored scenes for one. No band
    # folder is configured in the test environment, so the scene list is empty and nothing is read from disk ---
    listed = s.call("GET", "/api/v1/indices", 200)
    assert [i["key"] for i in listed.json()["indices"]] == ["ndvi"]
    found = s.call(
        "GET", "/api/v1/projects/{project_id}/indices/{index_key}/inputs", 200, path={**P, "index_key": "ndvi"}
    )
    assert found.json()["configured"] is False and found.json()["scenes"] == []
    s.call("GET", "/api/v1/projects/{project_id}/indices/{index_key}/inputs", 404, path={**P, "index_key": "nope"})
    # Runs are background jobs. Here no band folder exists, so a start is refused as an unknown scene and no job is made.
    runs = "/api/v1/projects/{project_id}/indices/{index_key}/runs"
    ndvi = {**P, "index_key": "ndvi"}
    assert s.call("GET", runs, 200, path=ndvi).json()["runs"] == []
    s.call("POST", runs, 404, path=ndvi, json={"scene_id": "0" * 16})
    s.call("POST", runs, 422, path=ndvi, json={"scene_id": "../../x"})
    ghost = {**ndvi, "job_id": "00000000-0000-4000-8000-000000000000"}
    s.call("GET", runs + "/{job_id}", 404, path=ghost)
    s.call("POST", runs + "/{job_id}/cancel", 404, path=ghost)

    off = s.call("GET", "/api/v1/imagery/s3/status", 200)
    assert off.json()["status"] == "DISABLED" and off.json()["authenticated"] is False
    s.call("POST", "/api/v1/imagery/s3/verify", 422, json={"unknown": True})
    s.call("POST", "/api/v1/imagery/s3/verify", 200, json={"confirm_external_request": True})
    s.call("POST", "/api/v1/workspace/profiles", 201, json={"display_name": "Second profile"}, headers=False)
    s.call("GET", "/api/v1/projects", 200)
    s.call("POST", "/api/v1/projects", 201, json={"name": "Another project"})
    s.call("GET", "/api/v1/projects/{project_id}", 200, path=P)
    s.call("PATCH", "/api/v1/projects/{project_id}", 200, path=P, json={"description": "contract test"})
    s.call("GET", "/api/v1/projects/{project_id}/summary", 200, path=P)

    # --- a photograph with a GPS candidate, a plain one, and a raster ---
    gps = media.image_bytes("JPEG", exif=media.gps_exif(), seed=31)
    uploaded = s.call(
        "POST",
        "/api/v1/projects/{project_id}/assets",
        201,
        path=P,
        params={"modality": "ground_image", "filename": "gps.jpg"},
        content=gps,
    ).json()["asset"]
    plain = photo(client, project, headers, run_jobs, name="plain.jpg")
    run_jobs()
    raster_bytes = media.geotiff_bytes()
    raster = s.call(
        "POST",
        "/api/v1/projects/{project_id}/assets",
        201,
        path=P,
        params={"modality": "overhead_raster", "filename": "r.tif"},
        content=raster_bytes,
    ).json()["asset"]
    unplaced = s.call(
        "POST",
        "/api/v1/projects/{project_id}/assets",
        201,
        path=P,
        params={"modality": "overhead_raster", "filename": "u.tif"},
        content=media.geotiff_bytes(crs=None, transform=None),
    ).json()["asset"]
    run_jobs()
    A = {"asset_id": uploaded["id"]}
    R = {"asset_id": raster["id"]}

    s.call("GET", "/api/v1/projects/{project_id}/assets", 200, path=P)
    s.call("GET", "/api/v1/assets/{asset_id}", 200, path=A)
    content = s.call("GET", "/api/v1/assets/{asset_id}/content", 200, path=A)
    assert content.content == gps
    preview = s.call("GET", "/api/v1/assets/{asset_id}/preview", 200, path=A, params={"variant": "view"})
    assert preview.content[:4] == b"RIFF" or preview.content[:2] == b"\xff\xd8" or preview.content[:4] == b"\x89PNG"

    # --- photo locations ---
    s.call("GET", "/api/v1/assets/{asset_id}/location", 200, path=A)
    s.call(
        "POST",
        "/api/v1/assets/{asset_id}/location",
        201,
        path=A,
        json={"action": "confirm_exif", "expected_version": 1, "confirm": True},
    )
    s.call("POST", "/api/v1/assets/{asset_id}/location/read-exif", 200, path={"asset_id": plain["id"]})
    view = s.call("GET", "/api/v1/assets/{asset_id}/camera-view", 200, path=A).json()
    assert view["heading_deg"] is None
    listing = s.call("GET", "/api/v1/projects/{project_id}/photo-locations", 200, path=P).json()
    assert listing["confirmed"] == 1

    # --- jobs, runs, observations, review ---
    created = s.call(
        "POST",
        "/api/v1/projects/{project_id}/jobs",
        202,
        path=P,
        json={"kind": "analyze", "asset_id": uploaded["id"], "pipeline": "ground_image", "model": STUB},
    ).json()
    assert run_jobs() == ["needs_review"]
    s.call("GET", "/api/v1/projects/{project_id}/jobs", 200, path=P)
    s.call("GET", "/api/v1/jobs", 200)
    job = s.call("GET", "/api/v1/jobs/{job_id}", 200, path={"job_id": created["id"]}).json()
    run_id = job["result"]["run_id"]
    s.call("GET", "/api/v1/runs/{run_id}", 200, path={"run_id": run_id})
    queued = analyze(client, project, headers, uploaded["id"])
    assert queued.status_code == 202
    s.call("POST", "/api/v1/jobs/{job_id}/cancel", 202, path={"job_id": queued.json()["id"]})
    observations = s.call("GET", "/api/v1/projects/{project_id}/observations", 200, path=P).json()
    assert observations
    oid = observations[0]["observation"]["observation_id"]
    s.call("GET", "/api/v1/observations/{observation_id}", 200, path={"observation_id": oid})
    s.call(
        "PATCH",
        "/api/v1/observations/{observation_id}/review",
        200,
        path={"observation_id": oid},
        json={"action": "confirm", "expected_revision": 0},
    )
    s.call("GET", "/api/v1/projects/{project_id}/conflicts", 200, path=P)

    # --- exports ---
    table = list(
        csv.reader(
            io.StringIO(
                s.call("GET", "/api/v1/projects/{project_id}/exports/observations.csv", 200, path=P).content.decode(
                    "utf-8-sig"
                )
            )
        )
    )
    assert len(table) >= 2
    geo = s.call("GET", "/api/v1/projects/{project_id}/exports/observations.geojson", 200, path=P).json()
    assert geo["type"] == "FeatureCollection" and any(
        f["properties"].get("feature_type") == "photo_location" for f in geo["features"]
    )
    exported = json.loads(s.call("GET", "/api/v1/projects/{project_id}/exports/observations.json", 200, path=P).content)
    assert {"observations", "photo_locations", "runs"} <= set(exported)
    findings = s.call("GET", "/api/v1/projects/{project_id}/report", 200, path=P).json()
    assert findings["read_only"] is True and findings["project"]["id"] == pid
    assert findings["summary"]["photos_total"] >= 1 and findings["limitations"]

    # --- map annotations ---
    A = "/api/v1/projects/{project_id}/annotations"
    drawn = {"type": "LineString", "coordinates": [[75.0, 27.0], [75.01, 27.0]]}
    note_one = s.call("POST", A, 201, path=P, json={"kind": "line", "geometry": drawn, "note": "fence"}).json()
    AID = {**P, "annotation_id": note_one["annotation_id"]}
    s.call("GET", A, 200, path=P)
    s.call("GET", A + "/{annotation_id}", 200, path=AID)
    edited = s.call("PATCH", A + "/{annotation_id}", 200, path=AID, json={"expected_version": 1, "note": "fence, east"})
    s.call("GET", A + "/{annotation_id}/history", 200, path=AID)
    s.call("DELETE", A + "/{annotation_id}", 200, path=AID, params={"expected_version": edited.json()["version_no"]})
    s.call("POST", A, 422, path=P, json={"kind": "point", "geometry": {"type": "Point", "coordinates": [75, 91]}})
    s.call("GET", A + "/{annotation_id}", 404, path={**P, "annotation_id": UNKNOWN_UUID})

    # --- 3D terrain: the list of rasters and whether each is elevation (the scenario's raster is imagery) ---
    terrain_list = s.call("GET", "/api/v1/projects/{project_id}/terrain", 200, path=P).json()
    assert terrain_list["usable"] == 0 and terrain_list["note"]

    # --- rasters, map, georeferencing ---
    layers = s.call("GET", "/api/v1/projects/{project_id}/layers", 200, path=P).json()
    assert [layer["asset_id"] for layer in layers["rasters"]] == [raster["id"]]
    s.call("GET", "/api/v1/assets/{asset_id}/raster-tools", 200, path=R)
    assessment = s.call("GET", "/api/v1/assets/{asset_id}/crs-assessment", 200, path=R).json()
    assert [status["code"] for status in assessment["statuses"] if status["active"]] == [
        "georeferenced",
        "crs_identified",
        "transformation_available",
    ]
    s.call("POST", "/api/v1/assets/{asset_id}/statistics", 202, path=R)
    run_jobs()
    s.call("GET", "/api/v1/assets/{asset_id}/display", 200, path=R)
    png = s.call("GET", "/api/v1/assets/{asset_id}/render.png", 200, path=R)
    assert png.content[:4] == b"\x89PNG"
    s.call("GET", "/api/v1/assets/{asset_id}/pixel", 200, path=R, params={"col": 2, "row": 2})
    s.call(
        "POST",
        "/api/v1/measurements",
        200,
        json={"kind": "distance", "space": "lonlat", "points": [[75.0, 27.0], [75.01, 27.0]]},
    )
    # A tile outside the raster is a documented, empty 204.
    tiles = "/api/v1/assets/{asset_id}/tiles/{z}/{x}/{y}.png"
    outside = s.call("GET", tiles, 204, path={"asset_id": raster["id"], "z": 0, "x": 0, "y": 0})
    assert outside.content == b""
    s.call("GET", "/api/v1/assets/{asset_id}/georeferencing", 200, path={"asset_id": unplaced["id"]})
    s.call(
        "POST",
        "/api/v1/assets/{asset_id}/georeferencing/validate",
        200,
        path={"asset_id": unplaced["id"]},
        json={"operation": "assign_crs", "crs_id": "EPSG:32643", "reason": "contract test"},
    )
    s.call("GET", "/api/v1/assets/{asset_id}/sidecars", 200, path={"asset_id": unplaced["id"]})

    # --- map sheets: a scanned picture and a PDF, their assessment, a note, and a control-point check ---
    sheet_png = s.call(
        "POST",
        "/api/v1/projects/{project_id}/map-sheets",
        201,
        path=P,
        params={"filename": "scan.png", "series": "esm", "sheet_label": "contract"},
        content=media.image_bytes("PNG", size=(120, 90), seed=77),
    ).json()
    pdf = (
        b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >> endobj\n"
        b"trailer << /Root 1 0 R >>\n%%EOF\n"
    )
    s.call(
        "POST",
        "/api/v1/projects/{project_id}/map-sheets",
        201,
        path=P,
        params={"filename": "sheet.pdf", "series": "osm"},
        content=pdf,
    )
    s.call("GET", "/api/v1/projects/{project_id}/map-sheets", 200, path=P)
    SHEET = {"asset_id": sheet_png["asset"]["id"]}
    s.call("GET", "/api/v1/assets/{asset_id}/map-sheet", 200, path=SHEET)
    s.call(
        "POST",
        "/api/v1/assets/{asset_id}/map-sheet/metadata-notes",
        201,
        path=SHEET,
        json={"field": "datum", "value": "named in the margin", "source": "contract test"},
    )
    checked = s.call(
        "POST",
        "/api/v1/assets/{asset_id}/control-points/validate",
        200,
        path=SHEET,
        json={
            "crs_id": "EPSG:32643",
            "points": [
                {"column": c, "row": r, "x": 500000 + 10 * c, "y": 3000000 - 10 * r}
                for c, r in ((0, 0), (120, 0), (120, 90), (0, 90))
            ],
            "source": "contract test",
            "reason": "contract test",
        },
    ).json()
    assert checked["valid"] is True and checked["fit"]["rms_pixels"] < 1e-6

    # --- accounting: every operation is exercised or explained, and nothing stale is listed ---
    known = {(method, path) for method, path, _ in OPERATIONS}
    unaccounted = sorted(known - s.exercised - set(NOT_EXERCISED))
    assert not unaccounted, f"operations neither exercised nor explained in NOT_EXERCISED: {unaccounted}"
    stale = sorted(set(NOT_EXERCISED) - known)
    assert not stale, f"NOT_EXERCISED names operations that no longer exist: {stale}"
    overlap = sorted(set(NOT_EXERCISED) & s.exercised)
    assert not overlap, f"listed as not exercised but exercised: {overlap}"
    assert re.match(r"^[0-9a-f-]{36}$", run_id) and uuid.UUID(run_id)
