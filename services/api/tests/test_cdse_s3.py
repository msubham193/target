"""CDSE S3 authentication check (geopoc.imagery.cdse_s3, s3sign, routers/imagery_s3). Entirely offline: the store is a mock
HTTP transport and the credentials are obvious fakes. No test opens a socket where the integration must stay silent, and
none downloads, lists beyond one key, writes, or touches the database."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from geopoc.api.main import app
from geopoc.api.routers.imagery import ImageryHooks, get_imagery_settings
from geopoc.api.routers.imagery import get_hooks as get_imagery_hooks
from geopoc.api.routers.imagery_s3 import get_s3_settings
from geopoc.config import Settings
from geopoc.imagery import cdse_s3
from geopoc.imagery.cdse_s3 import CdseS3Client, CdseS3Settings, describe, request_summary, verify
from geopoc.imagery.s3sign import sign_get
from tests.test_imagery import db_state, no_network

ACCESS = "FAKEACCESSKEYID0001"
SECRET = "fake/Secret+Key/for-tests-only/0001"
ENDPOINT = "https://s3.example.invalid"
NOW = datetime(2026, 11, 2, 9, 30, 15, tzinfo=UTC)
LISTING = b'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult><Name>eodata</Name><KeyCount>1</KeyCount></ListBucketResult>'


def s3_settings(**over) -> CdseS3Settings:
    values = {"enabled": True, "endpoint": ENDPOINT, "access_key": ACCESS, "secret_key": SECRET}
    values.update(over)
    return CdseS3Settings(_env_file=None, **values)  # type: ignore[call-arg]


def error_xml(code: str, message: str = "details") -> bytes:
    return f"<?xml version='1.0'?><Error><Code>{code}</Code><Message>{message}</Message></Error>".encode()


class Store:
    """A mock S3 endpoint. Each request gets the next scripted answer (the last repeats); every request is recorded."""

    def __init__(self, *answers):
        self.answers = list(answers) or [httpx.Response(200, content=LISTING)]
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers[min(len(self.requests) - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(store: Store, *, online=True, **over):
    return verify(s3_settings(**over), online_enabled=online, transport=store.transport, now=NOW)


def secret_absent(*texts: object) -> None:
    """For what is sent to the store: the access key id is part of the signed request, the secret key never is."""
    assert SECRET not in "\n".join(str(t) for t in texts)


def secrets_absent(*texts: object) -> None:
    blob = "\n".join(str(t) for t in texts)
    for secret in (ACCESS, SECRET):
        assert secret not in blob


# --- the signature --------------------------------------------------------------------------------------------------------


def test_signature_matches_the_example_published_by_aws():
    # "GET Bucket (List Objects)" in AWS's S3 documentation, "Authenticating Requests: Using the Authorization Header".
    headers = sign_get(
        host="examplebucket.s3.amazonaws.com",
        path="/",
        query={"max-keys": "2", "prefix": "J"},
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
    )
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"
    )
    assert headers["x-amz-date"] == "20130524T000000Z"


def test_the_secret_key_is_never_part_of_the_signed_headers():
    headers = sign_get(
        host="h", path="/eodata", query={}, access_key=ACCESS, secret_key=SECRET, region="default", now=NOW
    )
    secret_absent(headers)
    assert f"Credential={ACCESS}/20261102/default/s3/aws4_request" in headers["Authorization"]


# --- settings -------------------------------------------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    """No CDSE_S3_* in the environment (the suite pins inert placeholders there)."""
    for name in ("ENABLED", "ENDPOINT", "REGION", "ACCESS_KEY", "SECRET_KEY", "TIMEOUT_S"):
        monkeypatch.delenv(f"CDSE_S3_{name}", raising=False)


def test_the_integration_is_off_and_empty_by_default(clean_env):
    settings = CdseS3Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.enabled is False and settings.access_key is None and settings.secret_key is None
    assert settings.endpoint == "https://eodata.dataspace.copernicus.eu" and settings.region == "default"


def test_settings_are_read_from_the_documented_environment_variables(monkeypatch):
    for name, value in {
        "CDSE_S3_ENABLED": "true",
        "CDSE_S3_ENDPOINT": ENDPOINT,
        "CDSE_S3_REGION": "other",
        "CDSE_S3_ACCESS_KEY": ACCESS,
        "CDSE_S3_SECRET_KEY": SECRET,
    }.items():
        monkeypatch.setenv(name, value)
    settings = CdseS3Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.enabled and settings.endpoint == ENDPOINT and settings.region == "other"
    assert settings.access_key.get_secret_value() == ACCESS  # type: ignore[union-attr]
    assert settings.secret_key.get_secret_value() == SECRET  # type: ignore[union-attr]


def test_blank_placeholders_in_an_env_file_mean_not_set(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text(
        "CDSE_S3_ENABLED=false\nCDSE_S3_ENDPOINT=\nCDSE_S3_REGION=\nCDSE_S3_ACCESS_KEY=\nCDSE_S3_SECRET_KEY=\n",
        encoding="utf-8",
    )
    settings = CdseS3Settings(_env_file=env)  # type: ignore[call-arg]
    assert settings.endpoint == cdse_s3.DEFAULT_ENDPOINT and settings.region == "default"
    assert settings.access_key is None and settings.secret_key is None


def test_credentials_are_masked_when_the_settings_are_printed_or_dumped():
    settings = s3_settings()
    secrets_absent(repr(settings), str(settings), settings.model_dump(), settings.model_dump_json())


def test_the_example_env_file_holds_no_credential():
    text = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    lines = {k.strip(): v.strip() for k, _, v in (ln.partition("=") for ln in text.splitlines() if "=" in ln)}
    assert lines["CDSE_S3_ENABLED"] == "false"
    assert lines["CDSE_S3_ENDPOINT"] == "https://eodata.dataspace.copernicus.eu"
    assert lines["CDSE_S3_REGION"] == "default"
    assert lines["CDSE_S3_ACCESS_KEY"] == "" and lines["CDSE_S3_SECRET_KEY"] == ""


# --- state without any request ---------------------------------------------------------------------------------------------


def test_disabled_integration_reports_disabled_and_sends_nothing():
    store = Store()
    with no_network():
        result = verify(s3_settings(enabled=False), online_enabled=True, transport=store.transport)
    assert (result.status, result.reason, result.authenticated, result.enabled) == (
        "DISABLED",
        "switched_off",
        False,
        False,
    )
    assert store.requests == []


def test_a_closed_online_gate_blocks_verification():
    store = Store()
    with no_network():
        result = run(store, online=False)
    assert (result.status, result.reason) == ("DISABLED", "online_gate_closed")
    assert store.requests == []


@pytest.mark.parametrize("missing", ["access_key", "secret_key", "both", "blank"])
def test_missing_credentials_are_reported_before_any_request(missing):
    over = {
        "access_key": {"access_key": None},
        "secret_key": {"secret_key": None},
        "both": {"access_key": None, "secret_key": None},
        "blank": {"access_key": "   ", "secret_key": ""},
    }[missing]
    store = Store()
    with no_network():
        result = run(store, **over)
    assert (result.status, result.reason, result.configured) == ("MISSING_CREDENTIALS", "credentials_missing", False)
    assert store.requests == []


@pytest.mark.parametrize(
    "over",
    [
        {"access_key": "bad key!"},
        {"access_key": "a\nb\nc"},
        {"secret_key": "short"},
        {"secret_key": "has space in it ok"},
    ],
)
def test_malformed_credentials_are_refused_locally(over):
    store = Store()
    with no_network():
        result = run(store, **over)
    assert (result.status, result.reason) == ("MISSING_CREDENTIALS", "credentials_malformed")
    assert store.requests == []


@pytest.mark.parametrize(
    "over",
    [
        {"endpoint": "http://eodata.example.invalid"},  # plain http to a remote host
        {"endpoint": "https://user:pw@s3.example.invalid"},  # credentials in the address
        {"endpoint": "https://s3.example.invalid/some/path"},
        {"endpoint": "https://s3.example.invalid?x=1"},
        {"endpoint": "ftp://s3.example.invalid"},
        {"endpoint": "not a url"},
        {"region": "Bad Region!"},
    ],
)
def test_an_invalid_endpoint_or_region_is_refused_locally(over):
    store = Store()
    with no_network():
        result = run(store, **over)
    assert (result.status, result.reason, result.configured) == ("CONNECTION_FAILED", "invalid_endpoint", False)
    assert store.requests == []
    secrets_absent(result)
    assert "pw" not in result.endpoint


def test_describe_never_claims_authentication_and_makes_no_request():
    with no_network():
        result = describe(s3_settings(), online_enabled=True)
    assert (result.status, result.configured, result.authenticated, result.checked_at) == (
        "NOT_VERIFIED",
        True,
        False,
        None,
    )


def test_a_configured_but_disabled_integration_is_marked_configured():
    result = describe(s3_settings(enabled=False), online_enabled=True)
    assert (result.status, result.configured, result.enabled) == ("DISABLED", True, False)


# --- the one request ------------------------------------------------------------------------------------------------------


def test_successful_authentication_is_one_signed_read_only_listing_of_one_key():
    store = Store()
    result = run(store)
    assert (result.status, result.reason, result.authenticated, result.configured) == (
        "AUTHENTICATED",
        "authenticated",
        True,
        True,
    )
    assert result.checked_at is not None
    (request,) = store.requests
    assert request.method == "GET" and request.content == b""
    assert (request.url.scheme, request.url.host, request.url.path) == ("https", "s3.example.invalid", "/eodata")
    assert request.url.query.decode() == "delimiter=%2F&list-type=2&max-keys=1&prefix=Sentinel-2%2F"
    assert request.headers["authorization"].startswith(f"AWS4-HMAC-SHA256 Credential={ACCESS}/")
    assert request.headers["x-amz-content-sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    secret_absent(request.headers, request.url)
    assert "bearer" not in request.headers["authorization"].lower()


def test_the_signature_sent_is_the_one_the_signer_produces():
    store = Store()
    run(store)
    sent = store.requests[0]
    expected = sign_get(
        host="s3.example.invalid",
        path="/eodata",
        query={"list-type": "2", "prefix": "Sentinel-2/", "delimiter": "/", "max-keys": "1"},
        access_key=ACCESS,
        secret_key=SECRET,
        region="default",
        now=sent_time(sent),
    )
    assert sent.headers["authorization"] == expected["Authorization"]


def sent_time(request: httpx.Request) -> datetime:
    return datetime.strptime(request.headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def test_a_non_default_port_is_part_of_the_signed_host():
    store = Store()
    run(store, endpoint="https://s3.example.invalid:8443")
    request = store.requests[0]
    assert request.headers["host"] == "s3.example.invalid:8443"
    expected = sign_get(
        host="s3.example.invalid:8443",
        path="/eodata",
        query={"list-type": "2", "prefix": "Sentinel-2/", "delimiter": "/", "max-keys": "1"},
        access_key=ACCESS,
        secret_key=SECRET,
        region="default",
        now=sent_time(request),
    )
    assert request.headers["authorization"] == expected["Authorization"]


def test_the_client_offers_only_the_listing_probe_and_the_metadata_head():
    # Two read-only operations and nothing else: no get/put/delete/download method exists. (head_metadata is limited to
    # root metadata files by PRODUCT_KEY_RE; tests/test_cdse_s3_head.py.)
    public = [n for n in dir(CdseS3Client) if not n.startswith("_")]
    assert public == ["head_metadata", "probe"]
    secrets_absent(repr(CdseS3Client(s3_settings())))


@pytest.mark.parametrize(
    ("answer", "status", "reason"),
    [
        (httpx.Response(403, content=error_xml("InvalidAccessKeyId")), "AUTHENTICATION_FAILED", "invalid_access_key"),
        (
            httpx.Response(403, content=error_xml("SignatureDoesNotMatch")),
            "AUTHENTICATION_FAILED",
            "signature_mismatch",
        ),
        (httpx.Response(403, content=error_xml("AccessDenied")), "AUTHENTICATION_FAILED", "access_denied"),
        (httpx.Response(400, content=error_xml("ExpiredToken")), "AUTHENTICATION_FAILED", "credentials_expired"),
        (
            httpx.Response(400, content=error_xml("AuthorizationHeaderMalformed")),
            "AUTHENTICATION_FAILED",
            "authorization_malformed",
        ),
        (httpx.Response(403), "AUTHENTICATION_FAILED", "rejected"),
        (httpx.Response(401, content=b"<html>login</html>"), "AUTHENTICATION_FAILED", "rejected"),
        (httpx.Response(403, content=error_xml("SomethingNew")), "AUTHENTICATION_FAILED", "rejected"),
    ],
)
def test_invalid_credentials_are_classified_as_authentication_failures(answer, status, reason):
    result = run(Store(answer))
    assert (result.status, result.reason, result.authenticated) == (status, reason, False)


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(403, content=error_xml("RequestTimeTooSkewed")), "clock_skew"),
        (httpx.Response(404, content=error_xml("NoSuchBucket")), "bucket_not_found"),
        (httpx.Response(503, content=error_xml("SlowDown")), "rate_limited"),
        (httpx.Response(429), "rate_limited"),
        (httpx.Response(500, content=b"boom"), "server_error"),
        (httpx.Response(502), "server_error"),
        (httpx.Response(302, headers={"location": "https://elsewhere.example.invalid/"}), "unexpected_redirect"),
        (httpx.Response(404, content=b"not found"), "unexpected_response"),
        (httpx.Response(200, content=b"<html>Welcome to the hotel wifi</html>"), "unexpected_response"),
        (httpx.Response(200, content=b""), "unexpected_response"),
        (httpx.Response(200, content=b'{"ListBucketResult": 1}'), "unexpected_response"),
    ],
)
def test_other_answers_are_connection_problems_never_authentication(answer, reason):
    result = run(Store(answer))
    assert (result.status, result.reason, result.authenticated) == ("CONNECTION_FAILED", reason, False)


def test_a_clean_200_from_something_that_is_not_a_bucket_does_not_authenticate():
    # A TCP/TLS connection and a 200 are not authentication; only a bucket listing is.
    assert run(Store(httpx.Response(200, content=b"OK"))).status == "CONNECTION_FAILED"


@pytest.mark.parametrize("fault", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.PoolTimeout("slow")])
def test_a_timeout_is_a_connection_failure(fault):
    result = run(Store(fault))
    assert (result.status, result.reason, result.authenticated) == ("CONNECTION_FAILED", "timeout", False)


@pytest.mark.parametrize(
    "fault",
    [httpx.ConnectError("refused"), httpx.ReadError("reset"), httpx.RemoteProtocolError("bad"), httpx.ProxyError("x")],
)
def test_a_network_failure_is_a_connection_failure(fault):
    result = run(Store(fault))
    assert (result.status, result.reason, result.authenticated) == ("CONNECTION_FAILED", "unreachable", False)


def test_a_redirect_is_not_followed():
    store = Store(httpx.Response(307, headers={"location": "https://elsewhere.example.invalid/eodata"}))
    result = run(store)
    assert result.reason == "unexpected_redirect" and len(store.requests) == 1


def test_a_huge_answer_is_read_only_up_to_a_small_bound():
    served = []

    def stream():
        for _ in range(10_000):  # 10 MB offered
            served.append(1)
            yield b"x" * 1024

    result = run(Store(httpx.Response(200, content=stream())))
    assert result.status == "CONNECTION_FAILED" and len(served) < 200


def test_nothing_the_store_writes_reaches_the_result():
    nasty = error_xml(
        "InvalidAccessKeyId", f"key {ACCESS} secret {SECRET} <script>alert(1)</script> Bearer abc.def.ghi"
    )
    result = run(Store(httpx.Response(403, content=nasty)))
    secrets_absent(result, vars(result))
    assert "script" not in repr(result) and "Bearer" not in repr(result)
    assert result.message in cdse_s3.MESSAGES.values()


def test_every_reason_has_a_fixed_message_and_no_message_holds_a_credential_pattern():
    for message in cdse_s3.MESSAGES.values():
        assert (
            len(message) < 200
            and "AKIA" not in message
            and "secret_key" not in message.lower().replace("the secret key", "")
        )


def test_an_internal_fault_does_not_leak_the_credentials(caplog):
    caplog.set_level(logging.DEBUG)

    def broken(request):
        raise RuntimeError(f"unexpected failure with {ACCESS} and {SECRET} in the message")

    result = verify(s3_settings(), online_enabled=True, transport=httpx.MockTransport(broken), now=NOW)
    assert (result.status, result.reason) == ("CONNECTION_FAILED", "unreachable")
    secrets_absent(result, caplog.text, [r.getMessage() for r in caplog.records])


def test_logs_never_contain_credentials_in_any_outcome(caplog):
    caplog.set_level(logging.DEBUG)
    answers = [
        httpx.Response(200, content=LISTING),
        httpx.Response(403, content=error_xml("SignatureDoesNotMatch", f"{ACCESS} {SECRET}")),
        httpx.ConnectError(f"cannot reach {ACCESS}"),
        httpx.ReadTimeout(f"slow {SECRET}"),
    ]
    for answer in answers:
        run(Store(answer))
    run(Store(), enabled=False)
    assert "cdse-s3" in caplog.text
    secrets_absent(caplog.text)


def test_the_client_is_closed_after_the_check():
    closed = []

    class Tracking(httpx.MockTransport):
        def close(self) -> None:
            closed.append(True)

    store = Store()
    verify(s3_settings(), online_enabled=True, transport=Tracking(store), now=NOW)
    assert closed == [True]


def test_request_summary_describes_the_request_without_any_credential():
    summary = request_summary(s3_settings())
    assert summary["method"] == "GET" and summary["sends_project_or_area_data"] is False
    assert summary["url"] == f"{ENDPOINT}/eodata?delimiter=%2F&list-type=2&max-keys=1&prefix=Sentinel-2%2F"
    secrets_absent(summary)


# --- static guards ---------------------------------------------------------------------------------------------------------


def test_the_s3_code_cannot_write_download_or_reach_project_data():
    root = Path(__file__).resolve().parents[1] / "geopoc"
    for name in ("imagery/cdse_s3.py", "imagery/s3sign.py", "api/routers/imagery_s3.py"):
        source = (root / name).read_text(encoding="utf-8")
        code = "\n".join(ln for ln in source.splitlines() if not ln.lstrip().startswith("#"))
        assert not re.search(r"""["'](PUT|POST|DELETE|PATCH)["']""", code), name  # GET and HEAD are the only verbs
        assert not re.search(r"\b(?:session|engine|Session|get_engine|commit|\.add\()\b", code.replace("hooks", "")), (
            name
        )
        assert not re.search(
            r"(?m)^\s*(?:from|import)\s+(?:geopoc\.(?:satellite|earth_engine|geo|db|ingestion|storage)|shapely|rasterio|boto3|botocore)",
            code,
        ), name
        assert not re.search(r"\b(?:open|write_text|write_bytes|download|get_object|put_object)\(", code), name


# --- the API --------------------------------------------------------------------------------------------------------------


@pytest.fixture
def api(client, headers):
    """The real routes with these settings and a mock store; restored afterwards."""
    state = SimpleNamespace(
        s3=s3_settings(),
        app=Settings(_env_file=None, online_enabled=True),  # type: ignore[call-arg]
        store=Store(),
        client=client,
        headers=headers,
    )
    app.dependency_overrides[get_s3_settings] = lambda: state.s3
    app.dependency_overrides[get_imagery_settings] = lambda: state.app
    app.dependency_overrides[get_imagery_hooks] = lambda: ImageryHooks(transport=state.store.transport)
    yield state
    for dependency in (get_s3_settings, get_imagery_settings, get_imagery_hooks):
        app.dependency_overrides.pop(dependency, None)


STATUS_KEYS = {
    "enabled", "configured", "authenticated", "status", "endpoint", "region", "reason", "message", "online_gate_open", "checked_at",
}  # fmt: skip


def get_status(api):
    return api.client.get("/api/v1/imagery/s3/status", headers=api.headers)


def post_verify(api, **body):
    return api.client.post("/api/v1/imagery/s3/verify", json=body, headers=api.headers)


def test_status_of_the_default_installation_is_disabled(client, headers):
    response = client.get(
        "/api/v1/imagery/s3/status", headers=headers
    )  # real settings (the test environment pins them off)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DISABLED" and body["enabled"] is False and body["authenticated"] is False
    assert body["endpoint"] == "https://eodata.dataspace.copernicus.eu"
    assert set(body) == STATUS_KEYS


def test_status_reports_the_configuration_and_never_contacts_the_store(api):
    with no_network():
        response = get_status(api)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == STATUS_KEYS
    assert (body["status"], body["enabled"], body["configured"], body["authenticated"]) == (
        "NOT_VERIFIED",
        True,
        True,
        False,
    )
    assert body["endpoint"] == ENDPOINT and body["checked_at"] is None
    assert api.store.requests == []
    secrets_absent(response.text)


@pytest.mark.parametrize(
    ("over", "status"),
    [
        ({"enabled": False}, "DISABLED"),
        ({"access_key": None}, "MISSING_CREDENTIALS"),
        ({"endpoint": "http://x.example.invalid"}, "CONNECTION_FAILED"),
    ],
)
def test_status_reflects_each_configuration_state_without_a_request(api, over, status):
    api.s3 = s3_settings(**over)
    with no_network():
        response = get_status(api)
    assert response.json()["status"] == status and api.store.requests == []
    secrets_absent(response.text)


def test_status_with_the_online_gate_closed(api):
    api.app = Settings(_env_file=None, online_enabled=False)  # type: ignore[call-arg]
    body = get_status(api).json()
    assert (body["status"], body["reason"], body["online_gate_open"]) == ("DISABLED", "online_gate_closed", False)


def test_both_routes_need_a_workspace_profile(client):
    assert client.get("/api/v1/imagery/s3/status").status_code == 401
    assert client.post("/api/v1/imagery/s3/verify", json={"confirm_external_request": True}).status_code == 401


def test_verify_without_confirmation_is_refused_and_sends_nothing(api):
    with no_network():
        response = post_verify(api)
    assert response.status_code == 409 and response.json()["code"] == "CDSE_S3_CONFIRMATION_REQUIRED"
    assert "GET " + ENDPOINT + "/eodata" in response.json()["detail"]
    assert api.store.requests == []
    secrets_absent(response.text)


def test_verify_with_confirmation_checks_the_credentials_once(api):
    response = post_verify(api, confirm_external_request=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["authenticated"], body["reason"]) == ("AUTHENTICATED", True, "authenticated")
    assert set(body) == STATUS_KEYS | {"request"} and body["checked_at"].endswith("Z")
    assert body["request"]["method"] == "GET" and body["request"]["sends_project_or_area_data"] is False
    assert len(api.store.requests) == 1 and api.store.requests[0].method == "GET"
    secrets_absent(response.text)


def test_verify_reports_rejected_credentials_in_the_body(api):
    api.store = Store(httpx.Response(403, content=error_xml("InvalidAccessKeyId", f"{ACCESS} {SECRET}")))
    response = post_verify(api, confirm_external_request=True)
    body = response.json()
    assert response.status_code == 200 and (body["status"], body["authenticated"]) == ("AUTHENTICATION_FAILED", False)
    secrets_absent(response.text)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("down"), "unreachable")],
)
def test_verify_reports_timeouts_and_connection_failures(api, fault, reason):
    api.store = Store(fault)
    body = post_verify(api, confirm_external_request=True).json()
    assert (body["status"], body["reason"], body["authenticated"]) == ("CONNECTION_FAILED", reason, False)


@pytest.mark.parametrize(
    ("over", "status"),
    [({"enabled": False}, "DISABLED"), ({"secret_key": None}, "MISSING_CREDENTIALS")],
)
def test_verify_sends_nothing_when_disabled_or_credentials_are_missing(api, over, status):
    api.s3 = s3_settings(**over)
    with no_network():
        response = post_verify(api, confirm_external_request=True)
    assert response.status_code == 200 and response.json()["status"] == status and api.store.requests == []


def test_verify_sends_nothing_while_the_online_gate_is_closed(api):
    api.app = Settings(_env_file=None, online_enabled=False)  # type: ignore[call-arg]
    with no_network():
        response = post_verify(api, confirm_external_request=True)
    assert response.json()["reason"] == "online_gate_closed" and api.store.requests == []


def test_verify_rejects_unknown_fields(api):
    assert post_verify(api, confirm_external_request=True, project_id="x").status_code == 422


def test_neither_route_writes_to_the_database(api):
    before = db_state()
    get_status(api)
    post_verify(api)
    post_verify(api, confirm_external_request=True)
    api.store = Store(httpx.Response(403, content=error_xml("AccessDenied")))
    post_verify(api, confirm_external_request=True)
    assert db_state() == before


def test_the_openapi_document_describes_the_routes_without_a_credential_field():
    doc = app.openapi()
    assert {"/api/v1/imagery/s3/status", "/api/v1/imagery/s3/verify"} <= set(doc["paths"])
    properties = {
        name.lower()
        for schema_name, schema in doc["components"]["schemas"].items()
        if schema_name.startswith("CdseS3")
        for name in schema.get("properties", {})
    }
    assert {"status", "authenticated", "endpoint"} <= properties
    assert not {"access_key", "secret_key", "password", "token", "authorization", "secret"} & properties
