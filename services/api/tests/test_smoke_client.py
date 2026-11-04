"""The smoke-test client (geopoc.smoke): its write guard and identity checks, and the scratch checks it runs.

Dangerous cases use httpx.MockTransport and record every request, so the tests can state exactly what was (not) sent. The
real flow runs the client against the in-process application on the scratch database geopoc_test.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from geopoc import smoke
from geopoc.api.main import app
from geopoc.config import database_environment


def health_body(name, environment="scratch", extra=None):
    database = {"reachable": True, "schema_revision": "0008"}
    if name is not None:
        database.update(name=name, environment=environment, role_is_superuser=True)
    return {
        "status": "ok",
        "database": database,
        "storage": {},
        "worker": {"running": True},
        "versions": {},
        "online": {"enabled": False},
        "bind_host": "127.0.0.1",
        **(extra or {}),
    }


def fake_api(name, environment="scratch", url="http://127.0.0.1:8020"):
    """A client whose server only answers /health and records every request it receives."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json=health_body(name, environment))
        return httpx.Response(200, json={})

    return httpx.Client(base_url=url, transport=httpx.MockTransport(handler)), seen


# --- identity --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["geopoc", "postgres", "Geopoc_x", "geopoc_", "geopoc-x", "geopoc_x; drop database geopoc", "", "smoke"]
)
def test_scratch_mode_refuses_any_database_that_is_not_a_scratch_name(name):
    http, seen = fake_api(name, environment="scratch")  # even if the server CLAIMS scratch, the name decides
    client = smoke.GuardedClient(http, "scratch")
    with pytest.raises(smoke.TargetIdentityError):
        client.verify_identity()
    assert seen == [("GET", "/health")], "nothing but the identity request may be sent"


def test_scratch_mode_refuses_a_scratch_name_that_the_server_labels_working():
    http, seen = fake_api("geopoc_smoke", environment="working")
    with pytest.raises(smoke.TargetIdentityError):
        smoke.GuardedClient(http, "scratch").verify_identity()
    assert seen == [("GET", "/health")]


def test_scratch_mode_refuses_an_api_that_does_not_report_its_database():
    http, seen = fake_api(None)
    with pytest.raises(smoke.TargetIdentityError, match="does not report which database"):
        smoke.GuardedClient(http, "scratch").verify_identity()
    assert seen == [("GET", "/health")]


def test_scratch_mode_accepts_a_scratch_database_and_then_allows_writes():
    http, seen = fake_api("geopoc_smoke")
    client = smoke.GuardedClient(http, "scratch")
    identity = client.verify_identity()
    assert (identity.name, identity.environment, identity.reported) == ("geopoc_smoke", "scratch", True)
    client.post("/api/v1/projects", json={"name": "x"})
    assert ("POST", "/api/v1/projects") in seen


def test_a_write_before_identity_is_verified_is_refused_and_not_sent():
    http, seen = fake_api("geopoc_smoke")
    client = smoke.GuardedClient(http, "scratch")
    with pytest.raises(smoke.WriteRefused, match="has not been verified"):
        client.post("/api/v1/projects", json={})
    assert seen == []


def test_a_failed_identity_check_leaves_writes_refused():
    http, seen = fake_api("geopoc")
    client = smoke.GuardedClient(http, "scratch")
    with pytest.raises(smoke.TargetIdentityError):
        client.verify_identity()
    with pytest.raises(smoke.WriteRefused):
        client.patch("/api/v1/projects/00000000-0000-4000-8000-000000000001", json={"archived": True})
    assert seen == [("GET", "/health")]


# --- working mode is read-only -------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_working_mode_refuses_every_write_before_sending_it(method):
    http, seen = fake_api("geopoc", environment="working")
    client = smoke.GuardedClient(http, "working")
    client.verify_identity()
    with pytest.raises(smoke.WriteRefused, match="read-only"):
        client.request(method, "/api/v1/projects", json={"name": "x"})
    assert seen == [("GET", "/health")]


def test_working_mode_allows_get_and_head():
    http, seen = fake_api("geopoc", environment="working")
    client = smoke.GuardedClient(http, "working")
    client.verify_identity()
    assert client.get("/api/v1/projects").status_code == 200
    assert client.request("HEAD", "/api/v1/projects").status_code == 200
    assert [m for m, _ in seen] == ["GET", "GET", "HEAD"]


def test_working_mode_cannot_be_unlocked_by_a_scratch_looking_target():
    http, seen = fake_api("geopoc_smoke", environment="scratch")
    client = smoke.GuardedClient(http, "working")
    with pytest.raises(smoke.TargetIdentityError, match="reports a scratch database"):
        client.verify_identity()
    with pytest.raises(smoke.WriteRefused):
        client.post("/api/v1/projects", json={})
    assert seen == [("GET", "/health")]


def test_working_mode_accepts_an_older_api_that_does_not_report_its_name_but_stays_read_only():
    http, _ = fake_api(None)
    client = smoke.GuardedClient(http, "working")
    identity = client.verify_identity()
    assert identity.reported is False
    with pytest.raises(smoke.WriteRefused):
        client.post("/api/v1/projects", json={})


# --- connection and input safety ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://example.com", "http://10.0.0.5:8000", "http://192.168.1.2", "https://api.internal.local"]
)
def test_only_loopback_targets_are_accepted(url):
    with pytest.raises(smoke.SmokeError, match="only talks to this machine"):
        smoke.GuardedClient(httpx.Client(base_url=url), "scratch")


def test_unknown_mode_is_refused():
    with pytest.raises(smoke.SmokeError, match="Unknown mode"):
        smoke.GuardedClient(httpx.Client(base_url="http://127.0.0.1:8020"), "write-everything")


def test_timeouts_and_connection_failures_become_clear_messages():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    timed_out = smoke.GuardedClient(
        httpx.Client(base_url="http://127.0.0.1:8020", transport=httpx.MockTransport(slow)), "working", timeout_s=2
    )
    with pytest.raises(smoke.SmokeError, match=r"timed out after 2 s"):
        timed_out.get("/health")
    down = smoke.GuardedClient(
        httpx.Client(base_url="http://127.0.0.1:8020", transport=httpx.MockTransport(refused)), "working"
    )
    with pytest.raises(smoke.SmokeError, match="Is the API running"):
        down.get("/health")


def test_a_target_that_does_not_return_a_health_document_is_not_trusted():
    http = httpx.Client(
        base_url="http://127.0.0.1:8020",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>hello</html>")),
    )
    with pytest.raises(smoke.TargetIdentityError, match="not trusted"):
        smoke.GuardedClient(http, "scratch").verify_identity()


def test_main_reports_a_refusal_with_exit_code_2(capsys):
    assert smoke.main(["--url", "http://example.com", "--mode", "scratch"]) == 2
    assert "SMOKE TEST STOPPED" in capsys.readouterr().err


def test_main_reports_an_unreachable_api_with_exit_code_2(capsys):
    assert smoke.main(["--url", "http://127.0.0.1:9", "--mode", "working", "--timeout", "1"]) == 2
    assert "SMOKE TEST STOPPED" in capsys.readouterr().err


# --- the scratch rule is one rule everywhere ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "geopoc",
        "geopoc_test",
        "geopoc_e2e",
        "geopoc_smoke",
        "geopoc_script_test",
        "Geopoc_x",
        "geopoc_",
        "geopoc-x",
        "",
        None,
        "postgres",
        "geopoc_X",
    ],
)
def test_the_client_and_the_server_agree_on_what_a_scratch_database_is(name):
    assert smoke.is_scratch_name(name) == (database_environment(name) == "scratch")


# --- the real flow against the in-process application (scratch database geopoc_test) -----------------------------------


def test_health_reports_the_database_it_is_connected_to(client):
    database = client.get("/health").json()["database"]
    assert database["name"] == "geopoc_test"
    assert database["environment"] == "scratch"
    assert isinstance(database["role_is_superuser"], bool)


def test_the_full_scratch_smoke_run_passes_against_the_application(capsys):
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        client = smoke.GuardedClient(test_client, "scratch")
        report = smoke.run(client)
    output = capsys.readouterr().out
    failures = [name for name, ok, _ in report.results if not ok]
    assert failures == [], f"smoke checks failed: {failures}\n{output[-1500:]}"
    ran = {name for name, _, _ in report.results}
    for expected in (
        "health is ok",
        "EXIF read as an unverified candidate",
        "candidate is not the current location",
        "original stored byte-for-byte",
        "EXIF candidate confirmed by a person",
        "manual placement recorded",
        "map-selection placement recorded",
        "GeoJSON: three Point features in longitude, latitude order",
        "original unchanged after location changes",
    ):
        assert expected in ran, f"the smoke run did not include the check {expected!r}"
    assert "geopoc_test" in output


def test_the_working_mode_run_against_a_scratch_application_is_refused_not_run(capsys):
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        client = smoke.GuardedClient(test_client, "working")
        with pytest.raises(smoke.TargetIdentityError):
            smoke.run(client)


def test_working_mode_makes_only_get_requests_when_checking_a_working_looking_api(capsys):
    """Serve the real application's answers but present it as the working database, and record every method used."""
    methods: list[str] = []
    with TestClient(app, base_url="http://127.0.0.1") as test_client:

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            real = test_client.request(
                request.method, request.url.raw_path.decode(), content=request.content, headers=dict(request.headers)
            )
            body = real.content
            if request.url.path == "/health":
                data = json.loads(body)
                data["database"].update(name="geopoc", environment="working")
                body = json.dumps(data).encode()
            return httpx.Response(
                real.status_code, content=body, headers={"content-type": real.headers["content-type"]}
            )

        http = httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler))
        report = smoke.run(smoke.GuardedClient(http, "working"))
    assert set(methods) == {"GET"}, f"working mode sent {set(methods)}"
    assert report.failed == 0, [r for r in report.results if not r[1]]


# --- requests must stay on the verified target ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/health",
        "https://127.0.0.1:8000/api/v1/projects",
        "//example.com/health",
        "health",
        "",
        "ftp://127.0.0.1/x",
    ],
)
@pytest.mark.parametrize("mode", ["scratch", "working"])
def test_absolute_or_scheme_relative_urls_are_refused_before_sending(url, mode):
    http, seen = fake_api("geopoc_smoke" if mode == "scratch" else "geopoc", environment=mode)
    client = smoke.GuardedClient(http, mode)
    client.verify_identity()
    with pytest.raises(smoke.SmokeError, match="must be a path on the checked target"):
        client.get(url)
    assert seen == [("GET", "/health")], "nothing but the identity request may be sent"


def test_a_write_to_an_absolute_url_is_refused_in_scratch_mode_too():
    http, seen = fake_api("geopoc_smoke")
    client = smoke.GuardedClient(http, "scratch")
    client.verify_identity()
    with pytest.raises(smoke.SmokeError, match="must be a path"):
        client.post("http://example.com/api/v1/projects", json={})
    assert seen == [("GET", "/health")]
