"""CDSE S3 product metadata access check (HEAD of one root metadata object). Entirely offline: the store is a mock HTTP
transport, the credentials are obvious fakes, and no test opens a socket where the code must stay silent. A HEAD answer
has no body and the code never reads one; the key is restricted to the two metadata files at the root of a Sentinel-2
L2A product, so no image content can be named."""

import logging
from datetime import UTC, datetime

import httpx
import pytest

from geopoc.imagery import cdse_s3
from geopoc.imagery.cdse_s3 import (
    PRODUCT_KEY_RE,
    CdseS3Client,
    head_object,
    head_request_summary,
)
from geopoc.imagery.s3sign import sign_get
from tests.test_cdse_s3 import ACCESS, ENDPOINT, NOW, SECRET, Store, s3_settings, secrets_absent
from tests.test_imagery import no_network

# The product folder is the one in CDSE's own S3 documentation (documentation.dataspace.copernicus.eu/APIs/S3.html);
# MTD_MSIL2A.xml is the L2A product metadata file at the root of a SAFE folder (ESA product format).
FOLDER = "Sentinel-2/MSI/L2A/2024/01/15/S2A_MSIL2A_20240115T235221_N0510_R130_T55HGS_20240116T021554.SAFE"
KEY = f"{FOLDER}/MTD_MSIL2A.xml"
OK_HEADERS = {
    "content-length": "262144",
    "content-type": "application/xml",
    "last-modified": "Tue, 16 Jan 2024 02:30:00 GMT",
    "etag": '"d41d8cd98f00b204e9800998ecf8427e"',
}


def head(store: Store, key: str = KEY, *, online=True, **over):
    return head_object(s3_settings(**over), key, online_enabled=online, transport=store.transport, now=NOW)


def answering(code: int, **headers) -> httpx.Response:
    return httpx.Response(code, headers=headers)


# --- the signature --------------------------------------------------------------------------------------------------------


def test_head_and_get_are_signed_differently_and_only_those_two_methods_are_signed():
    common = dict(host="h", path="/eodata/x", query={}, access_key=ACCESS, secret_key=SECRET, region="default", now=NOW)
    assert sign_get(method="HEAD", **common)["Authorization"] != sign_get(method="GET", **common)["Authorization"]
    assert sign_get(**common) == sign_get(method="GET", **common)  # GET stays the default
    for method in ("PUT", "POST", "DELETE", "PATCH", "OPTIONS", "get"):
        with pytest.raises(ValueError):
            sign_get(method=method, **common)


def test_the_head_signature_is_the_aws_algorithm_for_a_head_request():
    # Recomputed independently from the SigV4 specification, not through sign_get.
    import hashlib
    import hmac

    empty = hashlib.sha256(b"").hexdigest()
    path = f"/eodata/{KEY}"
    amz = "20261102T093015Z"
    canonical = f"HEAD\n{path}\n\nhost:s3.example.invalid\nx-amz-content-sha256:{empty}\nx-amz-date:{amz}\n\nhost;x-amz-content-sha256;x-amz-date\n{empty}"
    scope = "20261102/default/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = ("AWS4" + SECRET).encode()
    for part in ("20261102", "default", "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    expected = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    store = Store(answering(200, **OK_HEADERS))
    head(store)
    assert store.requests[0].headers["authorization"].endswith(f"Signature={expected}")


# --- what is sent ---------------------------------------------------------------------------------------------------------


def test_one_signed_head_of_exactly_the_named_object_and_nothing_else():
    store = Store(answering(200, **OK_HEADERS))
    result = head(store)
    assert result.status == "ACCESSIBLE"
    (request,) = store.requests
    assert request.method == "HEAD" and request.content == b""
    assert (request.url.scheme, request.url.host) == ("https", "s3.example.invalid")
    assert request.url.path == f"/eodata/{KEY}" and request.url.query == b""
    assert request.headers["authorization"].startswith(f"AWS4-HMAC-SHA256 Credential={ACCESS}/20261102/default/s3/")
    assert request.headers["x-amz-content-sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert "range" not in request.headers and "content-length" not in request.headers
    assert SECRET not in str(request.headers) + str(request.url)


def test_the_summary_names_the_exact_request_for_the_default_endpoint():
    summary = head_request_summary(s3_settings(endpoint=cdse_s3.DEFAULT_ENDPOINT), KEY)
    assert summary["method"] == "HEAD" and summary["host"] == "eodata.dataspace.copernicus.eu"
    assert summary["path"] == f"/eodata/{KEY}"
    assert summary["url"] == f"https://eodata.dataspace.copernicus.eu/eodata/{KEY}"
    assert summary["body"] == "none" and summary["sends_project_or_area_data"] is False
    secrets_absent(summary)


def test_the_summary_matches_what_is_actually_sent():
    store = Store(answering(200, **OK_HEADERS))
    head(store)
    assert str(store.requests[0].url) == head_request_summary(s3_settings(), KEY)["url"]


# --- which objects may be named -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["MTD_MSIL2A.xml", "manifest.safe"])
def test_the_root_metadata_files_of_an_l2a_product_are_allowed(name):
    assert PRODUCT_KEY_RE.match(f"{FOLDER}/{name}")


@pytest.mark.parametrize(
    "key",
    [
        f"{FOLDER}/GRANULE/L2A_T55HGS_A044999_20240115T235222/IMG_DATA/R10m/T55HGS_20240115T235221_B04_10m.jp2",  # content
        f"{FOLDER}/",  # the folder itself
        f"{FOLDER}/INSPIRE.xml",
        f"{FOLDER}/MTD_MSIL2A.xml?versionId=1",
        f"{FOLDER}/MTD_MSIL2A.xml/../GRANULE/x.jp2",
        f"{FOLDER}/../MTD_MSIL2A.xml",
        f"/{KEY}",
        f"{KEY} ",
        f"{KEY}\n",
        KEY.replace("Sentinel-2/MSI/L2A", "Sentinel-1/SAR/SLC"),
        KEY.replace("Sentinel-2/MSI/L2A", "Sentinel-2/MSI/L1C").replace("MSIL2A", "MSIL1C"),
        "eodata/" + KEY,
        "other-bucket/" + KEY,
        "",
    ],
)
def test_anything_else_is_refused_before_any_request(key):
    store = Store()
    with no_network():
        with pytest.raises(ValueError):
            head_object(s3_settings(), key, online_enabled=True, transport=store.transport, now=NOW)
        with pytest.raises(ValueError):
            head_request_summary(s3_settings(), key)
        with pytest.raises(ValueError):
            CdseS3Client(s3_settings(), transport=store.transport, now=NOW).head_metadata(key)
    assert store.requests == []


# --- the answers ----------------------------------------------------------------------------------------------------------


def test_an_existing_object_reports_only_validated_metadata():
    result = head(Store(answering(200, **OK_HEADERS)))
    assert (result.status, result.reason, result.http_status) == ("ACCESSIBLE", "accessible", 200)
    assert result.metadata == {
        "content_length": 262144,
        "content_type": "application/xml",
        "last_modified": "Tue, 16 Jan 2024 02:30:00 GMT",
        "etag": "d41d8cd98f00b204e9800998ecf8427e",
    }
    assert "No content was transferred" in result.message


def test_header_values_that_do_not_look_right_are_dropped_not_passed_on():
    nasty = answering(
        200,
        **{
            "content-length": "10",
            "content-type": f"text/<script>; x={SECRET}",
            "last-modified": f"yesterday {ACCESS}",
            "etag": f'"{SECRET} Bearer abc"',
            "x-amz-meta-secret": SECRET,
        },
    )
    result = head(Store(nasty))
    assert result.status == "ACCESSIBLE" and result.metadata == {"content_length": 10}
    secrets_absent(result, vars(result))


def test_a_200_without_a_numeric_length_is_not_accepted_as_an_object():
    assert head(Store(answering(200))).reason == "unexpected_response"
    assert head(Store(answering(200, **{"content-length": "abc"}))).reason == "unexpected_response"


@pytest.mark.parametrize(
    ("code", "status", "reason"),
    [
        (404, "NOT_FOUND", "not_found"),
        (403, "ACCESS_DENIED", "access_denied"),
        (401, "ACCESS_DENIED", "access_denied"),
        (301, "CONNECTION_FAILED", "moved"),
        (307, "CONNECTION_FAILED", "moved"),
        (429, "CONNECTION_FAILED", "rate_limited"),
        (500, "CONNECTION_FAILED", "server_error"),
        (503, "CONNECTION_FAILED", "server_error"),
        (400, "CONNECTION_FAILED", "unexpected_response"),
        (204, "CONNECTION_FAILED", "unexpected_response"),
    ],
)
def test_each_http_outcome_is_told_apart(code, status, reason):
    result = head(Store(answering(code, location="https://elsewhere.example.invalid/x")))
    assert (result.status, result.reason, result.http_status) == (status, reason, code)


def test_a_permission_error_is_never_reported_as_accessible():
    for code in (401, 403, 404):
        assert head(Store(answering(code))).status != "ACCESSIBLE"


def test_a_redirect_is_not_followed():
    store = Store(answering(301, location="https://elsewhere.example.invalid/eodata/x"))
    result = head(store)
    assert result.reason == "moved" and len(store.requests) == 1


@pytest.mark.parametrize("fault", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.PoolTimeout("slow")])
def test_a_timeout_is_a_connection_failure(fault):
    result = head(Store(fault))
    assert (result.status, result.reason, result.http_status) == ("CONNECTION_FAILED", "timeout", None)


@pytest.mark.parametrize(
    "fault", [httpx.ConnectError("refused"), httpx.ReadError("reset"), httpx.RemoteProtocolError("x")]
)
def test_a_network_failure_is_a_connection_failure(fault):
    result = head(Store(fault))
    assert (result.status, result.reason, result.http_status) == ("CONNECTION_FAILED", "unreachable", None)


# --- no content, no leaks, no writes ---------------------------------------------------------------------------------------


def test_no_response_body_is_ever_read():
    pulled = []

    def stream():
        for _ in range(1000):
            pulled.append(1)
            yield b"x" * 1024

    result = head(Store(httpx.Response(200, headers=OK_HEADERS, content=stream())))
    assert result.status == "ACCESSIBLE" and pulled == []


def test_a_body_sent_by_mistake_does_not_reach_the_result():
    body = f"<Error><Code>AccessDenied</Code><Message>{SECRET}</Message></Error>".encode()
    result = head(Store(httpx.Response(403, content=body)))
    secrets_absent(result, vars(result))
    assert result.message in {**cdse_s3.MESSAGES, **cdse_s3.HEAD_MESSAGES}.values()


@pytest.mark.parametrize(
    ("over", "online", "status"),
    [
        ({"enabled": False}, True, "DISABLED"),
        ({}, False, "DISABLED"),
        ({"access_key": None}, True, "MISSING_CREDENTIALS"),
        ({"secret_key": ""}, True, "MISSING_CREDENTIALS"),
        ({"endpoint": "http://remote.example.invalid"}, True, "CONNECTION_FAILED"),
    ],
)
def test_nothing_is_sent_when_disabled_gated_or_unconfigured(over, online, status):
    store = Store()
    with no_network():
        result = head(store, online=online, **over)
    assert result.status == status and result.http_status is None and result.metadata == {}
    assert store.requests == []
    secrets_absent(result)


def test_logs_and_representations_never_hold_the_credentials(caplog):
    caplog.set_level(logging.DEBUG)
    answers = [
        answering(200, **OK_HEADERS),
        answering(403),
        httpx.ConnectError(f"cannot reach {ACCESS}"),
        httpx.ReadTimeout(f"slow {SECRET}"),
    ]
    results = [head(Store(a)) for a in answers]
    assert "cdse-s3 head" in caplog.text
    secrets_absent(caplog.text, results, repr(CdseS3Client(s3_settings())))


def test_an_internal_fault_does_not_leak_the_credentials(caplog):
    caplog.set_level(logging.DEBUG)

    def broken(request):
        raise RuntimeError(f"unexpected {ACCESS} {SECRET}")

    result = head_object(s3_settings(), KEY, online_enabled=True, transport=httpx.MockTransport(broken), now=NOW)
    assert (result.status, result.reason) == ("CONNECTION_FAILED", "unreachable")
    secrets_absent(result, caplog.text)


def test_the_head_code_has_no_database_or_object_content_access():
    import re

    from tests.test_cdse_s3 import Path  # noqa: F401  (re-exported; keeps the guard next to the other static test)

    source = (Path(cdse_s3.__file__)).read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*(?:from|import)\s+(?:sqlalchemy|geopoc\.db|boto3|botocore)", source)
    assert not re.search(
        r"\b(?:get_object|put_object|delete_object|download_file|iter_bytes\(\s*\)|\.read\(\))", source
    )
    assert datetime.now(UTC).year >= 2026  # the module imports cleanly under the pinned clock above
    assert ENDPOINT.startswith("https://")
