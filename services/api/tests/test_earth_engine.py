"""GEE-1: organisation-owned authentication, the read-only catalogue and the connection check, in operating model A.

Everything runs offline against stand-ins for the `ee` library and for Google auth: no network, no credential, no Cloud
project. These tests prove what OUR code asks for and refuses; they prove nothing about the live service, which has not been
reached from this host (no organisation credentials exist here). The API tests use the scratch database geopoc_test, only to
identify a local profile; nothing is written to it by Earth Engine.
"""

import json
import os
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import text

from geopoc.api.main import app
from geopoc.api.routers.earth_engine import EeHooks, get_ee_settings, get_hooks
from geopoc.config import REPO_ROOT, Settings
from geopoc.db.session import get_engine
from geopoc.earth_engine import catalogue, client, guard, killswitch, verify
from geopoc.earth_engine.control import ControlPaths
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.earth_engine.runtime import developers, get_runtime, reset_runtimes
from geopoc.earth_engine.usage import ABSOLUTE_MAX, Ceilings, UsageLog, verify_file
from geopoc.satellite.earthengine import EarthEngineProvider
from geopoc.satellite.gate import ProviderUnavailable

API_DIR = Path(__file__).resolve().parents[1]
SECRET_TOKEN = "ya29.a0AfH6SMBsecretsecretsecret-abc_123"
SECRET_KEY = (
    "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n-----END PRIVATE KEY-----\n"
)
PROJECT = "designated-test-project"
DEV = "Dev One"
EMAIL = "svc@designated-test-project.iam.gserviceaccount.com"

CREDENTIAL_MODULES = {
    "attached": "google.auth.compute_engine.credentials",
    "impersonated": "google.auth.impersonated_credentials",
    "external": "google.auth.identity_pool",
    "key": "google.oauth2.service_account",
    "user": "google.oauth2.credentials",
}


@pytest.fixture(autouse=True)
def _release_locks():
    yield
    reset_runtimes()


@contextmanager
def no_network():
    def refuse(*_a, **_k):
        raise AssertionError("a network connection was attempted")

    with mock.patch.object(socket.socket, "connect", refuse), mock.patch.object(socket, "getaddrinfo", refuse):
        yield


def make_credentials(kind: str):
    attributes = {"__module__": CREDENTIAL_MODULES[kind]}
    if kind != "user":
        attributes["service_account_email"] = EMAIL
    return type("Credentials", (), attributes)()


class FakeAuth:
    def __init__(self, kind="attached", error: Exception | None = None):
        self.kind, self.error, self.calls = kind, error, []

    def default(self, scopes):
        self.calls.append(("default", tuple(scopes)))
        if self.error:
            raise self.error
        return make_credentials(self.kind)

    def from_key_file(self, path, scopes):
        self.calls.append(("from_key_file", path, tuple(scopes)))
        if self.error:
            raise self.error
        return make_credentials("key")


class _Number:
    def __init__(self, ee, value):
        self.ee, self.value = ee, value

    def add(self, other):
        return _Number(self.ee, self.value + other)

    def getInfo(self):  # noqa: N802
        self.ee.calls.append("Number.getInfo")
        if self.ee.ping_error:
            raise self.ee.ping_error
        return self.ee.ping_answer if self.ee.ping_answer is not None else self.value


class _Collection:
    def __init__(self, ee, identifier):
        self.ee, self.id = ee, identifier

    def aggregate_min(self, prop):
        return ("min", self.id, prop)

    def aggregate_max(self, prop):
        return ("max", self.id, prop)


class _Dictionary:
    def __init__(self, ee, mapping):
        self.ee, self.mapping = ee, mapping

    def getInfo(self):  # noqa: N802
        self.ee.calls.append("Dictionary.getInfo")
        identifier = self.mapping["start"][1]
        if identifier in self.ee.probe_errors:
            raise self.ee.probe_errors[identifier]
        start, end = self.ee.extents.get(identifier, (None, None))
        return {"start": start, "end": end}


class FakeEE:
    __version__ = "1.7.50-fake"

    def __init__(self, *, init_error=None, ping_error=None, ping_answer=None, on_init=None, probe_errors=None):
        self.calls: list = []
        self.init_error, self.ping_error, self.ping_answer, self.on_init = init_error, ping_error, ping_answer, on_init
        self.probe_errors = probe_errors or {}
        base = int(datetime(2017, 3, 28, tzinfo=UTC).timestamp() * 1000)
        recent = int(datetime(2026, 10, 30, tzinfo=UTC).timestamp() * 1000)
        self.extents = {d.id: (base, recent) for d in catalogue.DATASETS}

    def Initialize(self, credentials=None, project=None):  # noqa: N802
        self.calls.append(("Initialize", type(credentials).__module__, project))
        if self.on_init:
            self.on_init()
        if self.init_error:
            raise self.init_error

    def Number(self, value):  # noqa: N802
        return _Number(self, value)

    def ImageCollection(self, identifier):  # noqa: N802
        self.calls.append(("ImageCollection", identifier))
        return _Collection(self, identifier)

    def Dictionary(self, mapping):  # noqa: N802
        return _Dictionary(self, mapping)

    def Geometry(self, *_a, **_k):  # noqa: N802
        raise AssertionError("a geometry was sent to Earth Engine")

    def Filter(self, *_a, **_k):  # noqa: N802
        raise AssertionError("a filter (and so possibly an area) was sent")

    def _mentions(self, name: str) -> bool:
        return any(c == name or (isinstance(c, tuple) and c[0] == name) for c in self.calls)


def make_settings(tmp_path: Path, **over) -> Settings:
    values = {
        "online_enabled": True,
        "earth_engine_enabled": True,
        "earth_engine_project": PROJECT,
        "earth_engine_control_dir": tmp_path / "ee-control",
        "earth_engine_developers": DEV,
    }
    values.update(over)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def control(tmp_path):
    paths = ControlPaths(tmp_path / "ee-control")
    paths.create()
    return paths


@pytest.fixture
def ready(tmp_path, control):
    """(settings, runtime) for a fully configured controlled evaluation."""
    settings = make_settings(tmp_path)
    return settings, get_runtime(settings)


def lines(paths: ControlPaths) -> list[dict]:
    if not paths.usage_log.exists():
        return []
    return [json.loads(line) for line in paths.usage_log.read_text(encoding="utf-8").splitlines()]


# --- defaults and ceilings ---------------------------------------------------------------------------------------------


def test_everything_is_off_by_default_and_the_ceilings_are_low():
    defaults = Settings(_env_file=None)  # type: ignore[call-arg]
    assert defaults.online_enabled is False and defaults.earth_engine_enabled is False
    assert defaults.earth_engine_project is None and defaults.earth_engine_control_dir is None
    assert defaults.earth_engine_developers == "" and developers(defaults) == frozenset()
    assert defaults.earth_engine_auth_mode == "adc" and defaults.earth_engine_allow_key_file is False
    ceilings = Ceilings.from_settings(defaults)
    assert ceilings == Ceilings(per_day=40, per_operation=3, concurrent=1)
    assert ceilings.per_day <= ABSOLUTE_MAX.per_day and ceilings.concurrent == 1


@pytest.mark.parametrize(
    "over",
    [
        {"earth_engine_max_requests_per_day": ABSOLUTE_MAX.per_day + 1},
        {"earth_engine_max_requests_per_operation": ABSOLUTE_MAX.per_operation + 1},
        {"earth_engine_max_concurrent": 2},
        {"earth_engine_max_requests_per_day": 0},
        {"earth_engine_max_concurrent": 0},
    ],
)
def test_a_ceiling_can_be_lowered_but_never_raised_past_its_bound(over):
    with pytest.raises(EarthEngineRefused) as caught:
        Ceilings.from_settings(Settings(_env_file=None, **over))  # type: ignore[call-arg]
    assert caught.value.code == "EE_NOT_CONFIGURED"
    assert Ceilings.from_settings(Settings(_env_file=None, earth_engine_max_requests_per_day=5)).per_day == 5  # type: ignore[call-arg]


def test_importing_the_application_and_the_package_loads_neither_ee_nor_google_auth():
    code = (
        "import sys, geopoc.api.main, geopoc.earth_engine.verify, geopoc.earth_engine.catalogue, geopoc.earth_engine.__main__;"
        "bad = [m for m in ('ee', 'google.auth', 'google.oauth2') if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_DIR,
        env={**os.environ, "PYTHONPATH": str(API_DIR)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- the kill switch ---------------------------------------------------------------------------------------------------


def test_the_kill_switch_stops_an_operation_before_anything_is_sent(ready, control):
    settings, runtime = ready
    killswitch.engage(control, "tester", "drill")
    ee = FakeEE()
    with no_network(), pytest.raises(EarthEngineRefused) as caught:
        verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())
    assert caught.value.code == "EE_KILL_SWITCH"
    assert ee.calls == [] and runtime.last_verification is None


def test_nothing_resumes_by_itself_and_resume_is_deliberate(ready, control):
    settings, runtime = ready
    killswitch.engage(control, "tester", "drill")
    killswitch.engage(control, "tester", "again")  # repeating is safe
    assert killswitch.state(control).engaged
    reset_runtimes()
    new_runtime = get_runtime(settings)  # a restart does not clear it
    with pytest.raises(EarthEngineRefused, match="kill switch"):
        verify.run_connection_test(new_runtime, DEV, ee_module=FakeEE(), auth=FakeAuth())
    assert killswitch.resume(control) is True and killswitch.resume(control) is False
    assert verify.run_connection_test(new_runtime, DEV, ee_module=FakeEE(), auth=FakeAuth())["ok"] is True


def test_the_kill_switch_fails_closed_when_it_cannot_be_read(tmp_path):
    missing = ControlPaths(tmp_path / "never-created")
    assert killswitch.state(missing).engaged and "fail closed" in killswitch.state(missing).reason
    paths = ControlPaths(tmp_path / "ctl")
    paths.create()
    assert not killswitch.state(paths).engaged
    with mock.patch("geopoc.earth_engine.killswitch.os.stat", side_effect=PermissionError("denied")):
        assert killswitch.state(paths).engaged  # cannot tell, so stopped
    with mock.patch.object(Path, "is_dir", side_effect=OSError("boom")):
        assert killswitch.state(paths).engaged  # never raises, and stopped


def test_the_kill_switch_is_checked_again_before_each_request_so_it_can_stop_a_running_check(ready, control):
    settings, runtime = ready
    ee = FakeEE(on_init=lambda: killswitch.engage(control, "tester", "mid-run"))
    with pytest.raises(EarthEngineRefused) as caught:
        verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())
    assert caught.value.code == "EE_KILL_SWITCH"
    assert ee._mentions("Initialize") and not ee._mentions("Number.getInfo")  # stopped before the computation
    assert runtime.last_verification is None
    final = [line for line in lines(control) if line["event"] == "commit"][-1]
    assert final["outcome"] == "error"  # the init may have been sent, so its units stay counted
    assert runtime.usage.used_today() == verify.COST


def test_a_kill_switch_stops_a_dataset_probe_too(ready, control):
    settings, runtime = ready
    ee = FakeEE()
    assert catalogue.probe(runtime, "landsat-8-c2-l2", DEV, ee_module=ee, auth=FakeAuth()).status == "available"
    killswitch.engage(control, "tester", "stop")
    before = list(ee.calls)
    with pytest.raises(EarthEngineRefused) as caught:
        catalogue.probe(runtime, "landsat-9-c2-l2", DEV, ee_module=ee, auth=FakeAuth())
    assert caught.value.code == "EE_KILL_SWITCH" and ee.calls == before


# --- the single-instance guard -----------------------------------------------------------------------------------------

HOLDER = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from geopoc.earth_engine.guard import guard_for\n"
    "from geopoc.earth_engine.errors import EarthEngineRefused\n"
    "g = guard_for(Path(sys.argv[1]))\n"
    "try:\n"
    "    g.acquire()\n"
    "except EarthEngineRefused as e:\n"
    "    print('REFUSED ' + e.code, flush=True); sys.exit(3)\n"
    "print('HELD', flush=True)\n"
    "if len(sys.argv) > 2:\n"
    "    sys.stdin.readline()\n"
)


def run_holder(lock: Path, *, stay: bool):
    args = [sys.executable, "-c", HOLDER, str(lock)] + (["stay"] if stay else [])
    env = {**os.environ, "PYTHONPATH": str(API_DIR)}
    return subprocess.Popen(
        args, cwd=API_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def test_a_second_process_is_refused_and_a_dead_holder_frees_the_lock(control):
    first = run_holder(control.lock_file, stay=True)
    try:
        assert first.stdout.readline().strip() == "HELD"
        second = run_holder(control.lock_file, stay=False)
        out, err = second.communicate(timeout=60)
        assert second.returncode == 3 and "REFUSED EE_INSTANCE_CONFLICT" in out, out + err
        # ...and this process is refused too, with the code the API reports
        with pytest.raises(EarthEngineRefused) as caught:
            guard.guard_for(control.lock_file).acquire()
        assert caught.value.code == "EE_INSTANCE_CONFLICT"
    finally:
        first.communicate("go\n", timeout=60)
    third = run_holder(control.lock_file, stay=False)  # the holder has exited: the operating system freed the lock
    out, err = third.communicate(timeout=60)
    assert third.returncode == 0 and out.strip() == "HELD", out + err
    guard.guard_for(control.lock_file).acquire()  # and so can this process


def test_an_operation_is_refused_while_another_process_holds_earth_engine(ready, control):
    settings, runtime = ready
    holder = run_holder(control.lock_file, stay=True)
    try:
        assert holder.stdout.readline().strip() == "HELD"
        ee = FakeEE()
        with pytest.raises(EarthEngineRefused) as caught:
            verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())
        assert caught.value.code == "EE_INSTANCE_CONFLICT" and caught.value.status == 409
        assert ee.calls == [] and lines(control) == []  # not even a reservation
    finally:
        holder.communicate("go\n", timeout=60)


def test_the_guard_is_idempotent_within_one_process_and_can_be_released(control):
    one = guard.guard_for(control.lock_file)
    one.acquire()
    one.acquire()
    assert one.held and guard.guard_for(control.lock_file) is one
    one.release()
    assert not one.held
    one.acquire()
    assert one.held


# --- budget control: reservations and ceilings -------------------------------------------------------------------------


class Clock:
    def __init__(self, now=datetime(2026, 11, 2, 9, 0, tzinfo=UTC)):
        self.now = now

    def __call__(self):
        return self.now


def test_the_daily_and_per_operation_ceilings_refuse_before_anything_is_sent(control):
    log = UsageLog(control, Ceilings(per_day=5, per_operation=3, concurrent=2), Clock())
    first = log.reserve("t", 3, "dev")
    log.commit(first, "ok")
    second = log.reserve("t", 2, "dev")
    log.commit(second, "ok")
    for units, why in ((1, "daily"), (4, "one operation")):
        with pytest.raises(EarthEngineRefused) as caught:
            log.reserve("t", units, "dev")
        assert caught.value.code == "EE_BUDGET_EXCEEDED" and caught.value.status == 429
        assert why in str(caught.value)
    assert log.used_today() == 5 and sum(1 for ln in lines(control) if ln["event"] == "reserve") == 2


def test_only_one_operation_may_run_at_a_time(control):
    log = UsageLog(control, Ceilings(per_day=10, per_operation=3, concurrent=1), Clock())
    running = log.reserve("t", 1, "dev")
    with pytest.raises(EarthEngineRefused, match="at a time"):
        log.reserve("t", 1, "dev")
    log.commit(running, "ok")
    log.commit(log.reserve("t", 1, "dev"), "ok")


def test_concurrent_callers_can_never_pass_the_daily_ceiling(control):
    log = UsageLog(control, Ceilings(per_day=5, per_operation=3, concurrent=64), Clock())
    gate, results = threading.Barrier(32), []

    def worker():
        gate.wait()
        try:
            results.append(log.reserve("t", 1, "dev"))
        except EarthEngineRefused as exc:
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(32)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    granted = [r for r in results if not isinstance(r, EarthEngineRefused)]
    assert len(granted) == 5 and len(results) == 32
    assert log.used_today() == 5 and sum(1 for ln in lines(control) if ln["event"] == "reserve") == 5


def test_concurrent_callers_can_never_pass_the_concurrency_ceiling(control):
    log = UsageLog(control, Ceilings(per_day=100, per_operation=3, concurrent=1), Clock())
    gate, results = threading.Barrier(24), []

    def worker():
        gate.wait()
        try:
            results.append(log.reserve("t", 1, "dev"))
        except EarthEngineRefused as exc:
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(24)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for r in results if not isinstance(r, EarthEngineRefused)) == 1  # nobody released, so exactly one


def test_usage_survives_a_restart_and_an_unfinished_reservation_counts_as_used(control):
    clock = Clock()
    log = UsageLog(control, Ceilings(10, 3, 5), clock)
    log.commit(log.reserve("a", 2, "dev"), "ok")
    log.release(log.reserve("b", 3, "dev"), "never sent")  # given back
    log.reserve("c", 1, "dev")  # the process "dies" here: no commit, no release
    again = UsageLog(control, Ceilings(10, 3, 5), clock)  # a restart replays the file
    assert again.used_today() == 3  # 2 committed + 1 unfinished; the released 3 do not count
    assert again.snapshot()["running_now"] == 0  # a dead process's reservation is used, not "running"


def test_the_day_rolls_over_in_utc(control):
    clock = Clock(datetime(2026, 11, 2, 23, 59, tzinfo=UTC))
    log = UsageLog(control, Ceilings(2, 2, 1), clock)
    log.commit(log.reserve("a", 2, "dev"), "ok")
    with pytest.raises(EarthEngineRefused):
        log.reserve("a", 1, "dev")
    clock.now += timedelta(minutes=2)
    log.commit(log.reserve("a", 2, "dev"), "ok")  # a new UTC day: a fresh allowance
    assert log.used_today() == 2


def test_the_log_is_append_only_and_hash_chained(control):
    log = UsageLog(control, Ceilings(10, 3, 1), Clock())
    log.commit(log.reserve("a", 1, "dev"), "ok")
    snapshot = control.usage_log.read_bytes()
    log.commit(log.reserve("b", 1, "dev"), "ok")
    assert control.usage_log.read_bytes().startswith(snapshot) and len(control.usage_log.read_bytes()) > len(snapshot)
    entries = lines(control)
    assert [e["seq"] for e in entries] == list(range(1, len(entries) + 1))
    assert entries[0]["prev"] == "0" * 64 and all(
        entries[i]["prev"] == entries[i - 1]["hash"] for i in range(1, len(entries))
    )
    assert verify_file(control)["ok"] is True


@pytest.mark.parametrize("damage", ["edit", "truncate", "delete_line", "garbage"])
def test_a_damaged_log_stops_earth_engine_until_the_owner_looks(control, damage):
    log = UsageLog(control, Ceilings(10, 3, 1), Clock())
    for name in ("a", "b", "c"):
        log.commit(log.reserve(name, 1, "dev"), "ok")
    raw = control.usage_log.read_text(encoding="utf-8").splitlines()
    if damage == "edit":
        assert '"units":1' in raw[0]  # the first line is a reservation; quietly lowering its units must be noticed
        raw[0] = raw[0].replace('"units":1', '"units":0')
    elif damage == "truncate":
        raw[-1] = raw[-1][: len(raw[-1]) // 2]
    elif damage == "delete_line":
        del raw[2]
    else:
        raw.append("not json at all")
    control.usage_log.write_text("\n".join(raw) + "\n", encoding="utf-8")
    with pytest.raises(EarthEngineRefused) as caught:
        UsageLog(control, Ceilings(10, 3, 1), Clock())
    assert caught.value.code == "EE_LOG_UNAVAILABLE" and "damaged" in str(caught.value)
    assert verify_file(control)["ok"] is False


def test_an_unreadable_log_fails_closed(control):
    control.usage_log.mkdir()  # something that cannot be read as a file
    with pytest.raises(EarthEngineRefused) as caught:
        UsageLog(control, Ceilings(10, 3, 1), Clock())
    assert caught.value.code == "EE_LOG_UNAVAILABLE"


def test_when_the_log_cannot_be_written_nothing_is_sent_and_nothing_is_counted(ready, control):
    settings, runtime = ready
    ee = FakeEE()
    verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())  # opens the log
    used, sent = runtime.usage.used_today(), len(ee.calls)
    with mock.patch("geopoc.earth_engine.usage.open", side_effect=OSError("disk full"), create=True):
        with pytest.raises(EarthEngineRefused) as caught:
            verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())
    assert caught.value.code == "EE_LOG_UNAVAILABLE"
    assert runtime.usage.used_today() == used and len(ee.calls) == sent


def test_the_log_holds_no_credential_no_area_and_no_signed_url(ready, control):
    settings, runtime = ready
    failing = FakeEE(
        init_error=RuntimeError(
            f"bad token {SECRET_TOKEN} {SECRET_KEY} https://earthengine.googleapis.com/x?token=SECRET"
        )
    )
    report = verify.run_connection_test(runtime, DEV, ee_module=failing, auth=FakeAuth())
    assert report["ok"] is False
    on_disk = control.usage_log.read_text(encoding="utf-8")
    for secret in (SECRET_TOKEN, "BEGIN PRIVATE KEY", "MIIEvQ", "token=SECRET", "earthengine.googleapis.com"):
        assert secret not in on_disk and secret not in json.dumps(report)
    allowed = {
        "seq",
        "at",
        "event",
        "prev",
        "hash",
        "id",
        "operation",
        "units",
        "actor",
        "request_id",
        "outcome",
        "actual_units",
        "actual_is_estimate",
        "detail",
        "code",
        "ok",
        "mode",
        "kind",
        "dataset",
        "status",
        "reason",
    }
    for entry in lines(control):
        assert set(entry) <= allowed, set(entry) - allowed


# --- fail closed: every precondition ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"online_enabled": False}, "EE_DISABLED"),
        ({"earth_engine_enabled": False}, "EE_DISABLED"),
        ({"earth_engine_project": None}, "EE_NOT_CONFIGURED"),
        ({"earth_engine_developers": "Someone Else"}, "EE_DEVELOPER_ONLY"),
        ({"earth_engine_developers": ""}, "EE_DEVELOPER_ONLY"),
    ],
)
def test_every_missing_precondition_is_a_refusal_before_anything_is_sent(tmp_path, control, over, code):
    runtime = get_runtime(make_settings(tmp_path, **over))
    ee = FakeEE()
    with no_network(), pytest.raises(EarthEngineRefused) as caught:
        verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())
    assert caught.value.code == code and ee.calls == []


def test_a_missing_or_misplaced_control_folder_is_a_refusal(tmp_path):
    with pytest.raises(EarthEngineRefused) as caught:
        get_runtime(
            Settings(_env_file=None, online_enabled=True, earth_engine_enabled=True, earth_engine_project=PROJECT)
        )  # type: ignore[call-arg]
    assert caught.value.code == "EE_NOT_CONFIGURED"
    runtime = get_runtime(make_settings(tmp_path))  # folder named but never created
    with pytest.raises(EarthEngineRefused) as caught:
        verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=FakeAuth())
    assert caught.value.code == "EE_CONTROL_UNAVAILABLE"
    inside = make_settings(tmp_path, earth_engine_control_dir=REPO_ROOT / "docs")  # inside the repository
    with pytest.raises(EarthEngineRefused) as caught:
        get_runtime(inside).paths.check_ready()
    assert caught.value.code == "EE_CONTROL_UNAVAILABLE" and "inside the repository" in str(caught.value)
    with pytest.raises(EarthEngineRefused):
        ControlPaths(REPO_ROOT / "x-control").create()
    assert not (REPO_ROOT / "x-control").exists()


def test_the_cli_owner_may_run_without_being_a_listed_developer_but_everything_else_still_applies(tmp_path, control):
    runtime = get_runtime(make_settings(tmp_path, earth_engine_developers=""))
    report = verify.run_connection_test(
        runtime, "cli:owner", ee_module=FakeEE(), auth=FakeAuth(), require_developer=False
    )
    assert report["ok"] is True


# --- authentication ----------------------------------------------------------------------------------------------------


def test_an_organisation_owned_identity_is_accepted_and_only_the_earth_engine_scope_is_requested(ready, control):
    settings, runtime = ready
    ee, auth = FakeEE(), FakeAuth("attached")
    report = verify.run_connection_test(runtime, DEV, ee_module=ee, auth=auth)
    assert report["ok"] and report["initialised"] and report["computation_ok"]
    assert report["credential_kind"] == "attached_service_account" and report["service_account_email"] == EMAIL
    assert auth.calls == [("default", (client.EE_SCOPE,))]
    assert ("Initialize", "google.auth.compute_engine.credentials", PROJECT) in ee.calls
    assert report["library_version"] == "1.7.50-fake" and runtime.last_verification == report
    assert runtime.usage.used_today() == verify.COST == client.COST_CONNECT + client.COST_PING


@pytest.mark.parametrize(
    ("mode", "kind", "ok"),
    [
        ("adc", "attached", True),
        ("adc", "impersonated", True),
        ("adc", "external", True),
        ("adc", "user", False),
        ("adc", "key", False),
        ("impersonation", "impersonated", True),
        ("impersonation", "attached", False),
    ],
)
def test_only_organisation_owned_identities_are_accepted(tmp_path, control, mode, kind, ok):
    runtime = get_runtime(make_settings(tmp_path, earth_engine_auth_mode=mode))
    report = verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=FakeAuth(kind))
    assert report["ok"] is ok
    if not ok:
        assert report["failure"]["code"] == "EE_AUTH_FAILED"
        assert report["initialised"] is False


def test_a_persons_own_google_account_is_refused_with_an_explanation(tmp_path, control):
    runtime = get_runtime(make_settings(tmp_path))
    report = verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=FakeAuth("user"))
    assert "individual person" in report["failure"]["message"]


def test_workload_identity_federation_needs_an_external_account_configuration(tmp_path, control, monkeypatch):
    runtime = get_runtime(make_settings(tmp_path, earth_engine_auth_mode="wif"))
    ee = FakeEE()
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(EarthEngineRefused, match="workload identity federation"):
        verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth("external"))
    assert ee.calls == []
    config = tmp_path / "wif.json"
    config.write_text(json.dumps({"type": "service_account", "private_key": SECRET_KEY}), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(config))
    with pytest.raises(EarthEngineRefused, match="external_account"):
        verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth("external"))
    config.write_text(json.dumps({"type": "external_account", "audience": "x"}), encoding="utf-8")
    assert verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth("external"))["credential_kind"] == (
        "external_account"
    )


def test_a_key_file_is_the_last_resort_and_needs_an_explicit_exception(tmp_path, control):
    key = tmp_path / "outside" / "key.json"
    key.parent.mkdir()
    key.write_text(json.dumps({"client_email": EMAIL, "private_key": SECRET_KEY}), encoding="utf-8")
    base = {"earth_engine_auth_mode": "key_file", "earth_engine_credentials_file": key}
    # not allowed without the switch
    runtime = get_runtime(make_settings(tmp_path, **base))
    with pytest.raises(EarthEngineRefused, match="last resort"):
        verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=FakeAuth())
    # allowed with the switch: the library gets the PATH; we never read the key
    reset_runtimes()
    runtime = get_runtime(make_settings(tmp_path, earth_engine_allow_key_file=True, **base))
    auth = FakeAuth()
    report = verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=auth)
    assert report["ok"] and report["credential_kind"] == "service_account_key"
    assert auth.calls == [("from_key_file", str(key), (client.EE_SCOPE,))]
    assert "private_key" not in repr(make_settings(tmp_path, **base)) + json.dumps(report)
    # a key kept inside the repository (here: pretend the repository is the folder holding it) is refused unread
    reset_runtimes()
    runtime = get_runtime(make_settings(tmp_path, earth_engine_allow_key_file=True, **base))
    untouched = FakeAuth()
    with mock.patch("geopoc.earth_engine.client.REPO_ROOT", key.parent):
        with pytest.raises(EarthEngineRefused, match="inside the repository or the data folder"):
            verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=untouched)
    assert untouched.calls == []  # refused before the library was given the file


def test_adc_that_resolves_to_a_key_is_not_a_way_around_the_key_file_rule(tmp_path, control):
    runtime = get_runtime(make_settings(tmp_path, earth_engine_auth_mode="adc"))
    report = verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=FakeAuth("key"))
    assert report["ok"] is False and "long-lived service-account key" in report["failure"]["message"]


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (RuntimeError("Caller does not have required permission serviceusage.services.use"), "serviceUsageConsumer"),
        (RuntimeError("Project is not registered to use Earth Engine"), "not registered for Earth Engine"),
        (RuntimeError("403 Forbidden: permission denied"), "earthengine.computations.create"),
        (RuntimeError("429 Too many requests"), "quota or rate limit"),
        (RuntimeError("something odd"), "reported an error"),
    ],
)
def test_initialisation_failures_say_what_to_fix_without_echoing_secrets(ready, control, error, needle):
    settings, runtime = ready
    error = type(error)(f"{error} {SECRET_TOKEN}")
    report = verify.run_connection_test(runtime, DEV, ee_module=FakeEE(init_error=error), auth=FakeAuth())
    assert report["ok"] is False and needle.lower() in report["failure"]["message"].lower()
    assert SECRET_TOKEN not in json.dumps(report) and SECRET_TOKEN not in control.usage_log.read_text(encoding="utf-8")


def test_auth_library_errors_are_redacted_and_become_a_reported_failure(ready, control):
    settings, runtime = ready
    auth = FakeAuth(error=RuntimeError(f"DefaultCredentialsError {SECRET_TOKEN}"))
    report = verify.run_connection_test(runtime, DEV, ee_module=FakeEE(), auth=auth)
    assert report["failure"]["code"] == "EE_AUTH_FAILED" and SECRET_TOKEN not in json.dumps(report)


def test_a_wrong_answer_to_the_trivial_computation_is_not_trusted(ready, control):
    settings, runtime = ready
    report = verify.run_connection_test(runtime, DEV, ee_module=FakeEE(ping_answer=3), auth=FakeAuth())
    assert report["initialised"] and not report["computation_ok"] and not report["ok"]


def test_a_missing_client_package_is_a_refusal_and_its_reservation_is_given_back(ready, control):
    settings, runtime = ready
    real = __import__("importlib").import_module

    def no_ee(name, *a, **k):
        if name == "ee":
            raise ImportError("no ee")
        return real(name, *a, **k)

    with mock.patch("geopoc.earth_engine.client.importlib.import_module", no_ee):
        with pytest.raises(EarthEngineRefused) as caught:
            verify.run_connection_test(runtime, DEV, auth=FakeAuth())
    assert caught.value.code == "EE_PACKAGE_MISSING" and "uv sync --inexact --group earthengine" in str(caught.value)
    assert runtime.usage.used_today() == 0  # nothing was sent, so nothing is counted


def test_repeated_checks_run_into_the_fixed_ceiling_and_stay_refused(tmp_path, control):
    runtime = get_runtime(make_settings(tmp_path, earth_engine_max_requests_per_day=6))
    ee = FakeEE()
    for _ in range(2):
        assert verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())["ok"]
    sent = len(ee.calls)
    with pytest.raises(EarthEngineRefused) as caught:
        verify.run_connection_test(runtime, DEV, ee_module=ee, auth=FakeAuth())
    assert caught.value.code == "EE_BUDGET_EXCEEDED" and len(ee.calls) == sent
    reset_runtimes()  # a restart does not reset the day's allowance
    again = get_runtime(make_settings(tmp_path, earth_engine_max_requests_per_day=6))
    with pytest.raises(EarthEngineRefused, match="daily ceiling"):
        verify.run_connection_test(again, DEV, ee_module=ee, auth=FakeAuth())


KINDS_SCRIPT = """
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import compute_engine, identity_pool, impersonated_credentials
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account
from geopoc.earth_engine import client

EMAIL = "svc@designated-test-project.iam.gserviceaccount.com"
pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
key = service_account.Credentials.from_service_account_info(
    {"client_email": EMAIL, "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"}
)
federated = identity_pool.Credentials(
    audience="//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/q",
    subject_token_type="urn:ietf:params:oauth:token-type:jwt",
    token_url="https://sts.googleapis.com/v1/token",
    credential_source={"file": "subject-token.txt"},
)
impersonated = impersonated_credentials.Credentials(
    source_credentials=key, target_principal=EMAIL, target_scopes=[client.EE_SCOPE]
)
print(json.dumps({
    "key": client.credential_kind(key),
    "federated": client.credential_kind(federated),
    "attached": client.credential_kind(compute_engine.Credentials()),
    "user": client.credential_kind(user_credentials.Credentials(token="t")),
    "impersonated": client.credential_kind(impersonated),
    "other": client.credential_kind(object()),
}))
"""


def test_credential_kinds_are_recognised_from_the_real_google_auth_classes():
    """The class-name logic is checked against the installed libraries, built offline (no refresh, no network). Run in a
    subprocess so the real Google modules are not left loaded in this process."""
    result = subprocess.run(
        [sys.executable, "-c", KINDS_SCRIPT],
        cwd=API_DIR,
        env={**os.environ, "PYTHONPATH": str(API_DIR)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-800:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "key": "service_account_key",
        "federated": "external_account",
        "attached": "attached_service_account",
        "user": "user",
        "impersonated": "impersonated_service_account",
        "other": "other",
    }


# --- the catalogue -----------------------------------------------------------------------------------------------------


def test_the_catalogue_holds_exactly_the_verified_datasets():
    assert {d.id for d in catalogue.DATASETS} == {
        "COPERNICUS/S2_SR_HARMONIZED",
        "COPERNICUS/S2_CLOUD_PROBABILITY",
        "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED",
        "COPERNICUS/S1_GRD",
        "LANDSAT/LC08/C02/T1_L2",
        "LANDSAT/LC09/C02/T1_L2",
    }
    assert len({d.slug for d in catalogue.DATASETS}) == len(catalogue.DATASETS)
    for d in catalogue.DATASETS:
        assert d.verified_on == "2026-10-31" and d.source_url.startswith("https://developers.google.com/earth-engine/")
        assert d.licence and d.attribution and d.bands and d.documented_start
        assert "documented_end" not in d.as_dict() and not any(
            "end" == k for k in d.as_dict()
        )  # ends are not hard-coded


def test_catalogue_facts_match_the_provider_pages():
    landsat = {b.name: b for b in catalogue.get("landsat-8-c2-l2").bands}
    assert (landsat["SR_B4"].scale, landsat["SR_B4"].offset) == (0.0000275, -0.2)
    assert (landsat["ST_B10"].scale, landsat["ST_B10"].offset, landsat["ST_B10"].units) == (0.00341802, 149.0, "K")
    assert catalogue.get("landsat-9-c2-l2").documented_start == "2021-10-31"
    s2 = {b.name: b for b in catalogue.get("COPERNICUS/S2_SR_HARMONIZED").bands}
    assert s2["B4"].pixel_size_m == 10 and s2["B11"].pixel_size_m == 20 and s2["B1"].pixel_size_m == 60
    assert s2["B8"].scale == 0.0001 and s2["SCL"].scale is None
    assert {b.name for b in catalogue.get("sentinel-1-grd").bands} == {"HH", "HV", "VV", "VH", "angle"}
    assert catalogue.get("cloud-score-plus-s2").licence == "CC-BY-4.0"


def test_listing_the_catalogue_is_local_only(ready, control):
    settings, runtime = ready
    with no_network(), mock.patch("geopoc.earth_engine.client.load_ee", side_effect=AssertionError("ee imported")):
        items = catalogue.listing(runtime.probes)
    assert len(items) == 6 and all(i["probe"]["status"] == "not_probed" and i["probe"]["live"] is False for i in items)
    assert lines(control) == []  # no usage charged, nothing logged


def test_a_probe_asks_one_question_about_the_collection_and_sends_no_geometry(ready, control):
    settings, runtime = ready
    ee = FakeEE()
    result = catalogue.probe(runtime, "sentinel-2-sr-harmonized", DEV, ee_module=ee, auth=FakeAuth())
    assert result.status == "available" and result.live is True
    assert result.extent_start == "2017-03-28T00:00:00Z" and result.extent_end == "2026-10-30T00:00:00Z"
    assert ("ImageCollection", "COPERNICUS/S2_SR_HARMONIZED") in ee.calls and ee._mentions("Dictionary.getInfo")
    assert runtime.usage.used_today() == catalogue.COST_PROBE + client.COST_CONNECT  # cold: connect + one query
    catalogue.probe(runtime, "landsat-9-c2-l2", DEV, ee_module=ee, auth=FakeAuth())
    assert runtime.usage.used_today() == 2 * catalogue.COST_PROBE + client.COST_CONNECT  # warm: one query
    assert runtime.probes["landsat-9-c2-l2"]["status"] == "available"
    listing = {i["slug"]: i["probe"]["status"] for i in catalogue.listing(runtime.probes)}
    assert listing["landsat-9-c2-l2"] == "available" and listing["sentinel-1-grd"] == "not_probed"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RuntimeError("Collection asset 'X' not found"), "not_found"),
        (RuntimeError("Permission denied on resource"), "permission_denied"),
        (RuntimeError("429: too many requests"), "quota"),
        (RuntimeError("weird failure"), "error"),
    ],
)
def test_probe_failures_are_classified_and_redacted(ready, control, error, status):
    settings, runtime = ready
    dataset = catalogue.get("sentinel-1-grd")
    ee = FakeEE(probe_errors={dataset.id: type(error)(f"{error} {SECRET_TOKEN}")})
    result = catalogue.probe(runtime, dataset.slug, DEV, ee_module=ee, auth=FakeAuth())
    assert result.status == status and SECRET_TOKEN not in result.detail
    assert runtime.probes[dataset.slug]["status"] == status


def test_an_empty_collection_is_not_reported_as_available(ready, control):
    settings, runtime = ready
    ee = FakeEE()
    ee.extents["LANDSAT/LC09/C02/T1_L2"] = (None, None)
    assert catalogue.probe(runtime, "landsat-9-c2-l2", DEV, ee_module=ee, auth=FakeAuth()).status == "error"


def test_an_unknown_dataset_is_refused_before_anything_is_reserved(ready, control):
    settings, runtime = ready
    with pytest.raises(EarthEngineRefused) as caught:
        catalogue.probe(runtime, "modis-everything", DEV, ee_module=FakeEE(), auth=FakeAuth())
    assert caught.value.code == "EE_UNKNOWN_DATASET" and lines(control) == []


# --- the API -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def api(client, tmp_path, control, headers):
    """The real routes with these settings and stand-ins; restored afterwards."""
    state = SimpleNamespace(settings=make_settings(tmp_path), hooks=EeHooks(ee_module=FakeEE(), auth=FakeAuth()))
    app.dependency_overrides[get_ee_settings] = lambda: state.settings
    app.dependency_overrides[get_hooks] = lambda: state.hooks
    yield SimpleNamespace(client=client, state=state, headers=headers, control=control)
    app.dependency_overrides.pop(get_ee_settings, None)
    app.dependency_overrides.pop(get_hooks, None)


def db_counts() -> dict[str, int]:
    with get_engine().connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("audit_event", "workspace_user", "project", "asset", "job")
        }


def test_status_needs_a_profile_and_shows_no_credential(client):
    assert client.get("/api/v1/earth-engine/status").status_code == 401


def test_status_when_nothing_is_configured_is_a_calm_local_answer(client, headers):
    with no_network():
        body = client.get("/api/v1/earth-engine/status", headers=headers).json()
    assert body["operating_model"] == "A" and body["earth_engine_enabled"] is False and body["online_enabled"] is False
    assert body["control_folder_ready"] is False and body["kill_switch_engaged"] is True  # fail closed
    assert body["usage"] is None and body["live_verified"] is False and body["you_are_listed"] is False
    assert body["project_configured"] is False and "note" in body


def test_status_when_configured_reports_local_state_only(api):
    with no_network():
        body = api.client.get("/api/v1/earth-engine/status", headers=api.headers).json()
    assert body["control_folder_ready"] is True and body["kill_switch_engaged"] is False
    assert body["project_configured"] is True and PROJECT not in json.dumps(body)  # the project id is not echoed
    assert body["usage"]["used_today"] == 0 and body["usage"]["ceilings"]["concurrent_operations"] == 1
    assert body["held_by_this_process"] is False  # a status read does not take the lock or charge usage
    assert api.state.hooks.ee_module.calls == []
    killswitch.engage(api.control, "t", "x")
    assert api.client.get("/api/v1/earth-engine/status", headers=api.headers).json()["kill_switch_engaged"] is True


def test_the_dataset_list_is_read_only_and_never_loads_the_client(api):
    with no_network(), mock.patch("geopoc.earth_engine.client.load_ee", side_effect=AssertionError("ee")):
        body = api.client.get("/api/v1/earth-engine/datasets", headers=api.headers).json()
    assert len(body["items"]) == 6 and {i["probe"]["status"] for i in body["items"]} == {"not_probed"}
    assert "documented_start" in body["items"][0] and "verified_on" in body["items"][0]
    assert api.client.get("/api/v1/earth-engine/datasets").status_code == 401


def test_the_connection_check_runs_for_a_listed_developer_and_writes_nothing_to_the_database(api):
    before = db_counts()
    response = api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers)
    assert (
        response.status_code == 403 and response.json()["code"] == "EE_DEVELOPER_ONLY"
    )  # "Test Reviewer" is not listed
    api.state.settings = make_settings(
        api.state.settings.earth_engine_control_dir.parent, earth_engine_developers="Test Reviewer"
    )
    response = api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["credential_kind"] == "attached_service_account" and body["failure"] is None
    assert db_counts() == before  # Earth Engine wrote nothing to PostgreSQL
    assert [e["event"] for e in lines(api.control)].count("reserve") == 1
    status = api.client.get("/api/v1/earth-engine/status", headers=api.headers).json()
    assert (
        status["live_verified"] is True and status["held_by_this_process"] is True and status["you_are_listed"] is True
    )
    assert status["usage"]["used_today"] == verify.COST


@pytest.mark.parametrize(
    ("change", "http", "code"),
    [
        ({"online_enabled": False}, 409, "EE_DISABLED"),
        ({"earth_engine_project": None}, 409, "EE_NOT_CONFIGURED"),
        ({"earth_engine_control_dir": None}, 409, "EE_NOT_CONFIGURED"),
    ],
)
def test_refusals_map_to_stable_codes_and_statuses(api, change, http, code):
    api.state.settings = make_settings(
        api.state.settings.earth_engine_control_dir.parent, earth_engine_developers="Test Reviewer", **change
    )
    response = api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers)
    assert (response.status_code, response.json()["code"]) == (http, code)
    assert api.state.hooks.ee_module.calls == []


def test_the_kill_switch_and_the_ceiling_refuse_over_http(api):
    api.state.settings = make_settings(
        api.state.settings.earth_engine_control_dir.parent,
        earth_engine_developers="Test Reviewer",
        earth_engine_max_requests_per_day=3,
    )
    assert api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers).status_code == 200
    over = api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers)
    assert over.status_code == 429 and over.json()["code"] == "EE_BUDGET_EXCEEDED"
    killswitch.engage(api.control, "t", "x")
    stopped = api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers)
    assert stopped.status_code == 409 and stopped.json()["code"] == "EE_KILL_SWITCH"


def test_a_failed_check_is_a_200_with_the_reason_and_leaks_nothing(api, caplog):
    api.state.settings = make_settings(
        api.state.settings.earth_engine_control_dir.parent, earth_engine_developers="Test Reviewer"
    )
    api.state.hooks = EeHooks(
        ee_module=FakeEE(init_error=RuntimeError(f"403 permission denied {SECRET_TOKEN} {SECRET_KEY}")), auth=FakeAuth()
    )
    caplog.set_level("DEBUG")
    response = api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers)
    assert response.status_code == 200 and response.json()["ok"] is False
    assert response.json()["failure"]["code"] == "EE_AUTH_FAILED"
    for text_ in (response.text, caplog.text, api.control.usage_log.read_text(encoding="utf-8")):
        assert SECRET_TOKEN not in text_ and "BEGIN PRIVATE KEY" not in text_


def test_the_probe_endpoint_validates_the_slug_and_sends_no_geometry(api):
    api.state.settings = make_settings(
        api.state.settings.earth_engine_control_dir.parent, earth_engine_developers="Test Reviewer"
    )
    assert api.client.post("/api/v1/earth-engine/datasets/NOT%20A%20SLUG/probe", headers=api.headers).status_code == 422
    unknown = api.client.post("/api/v1/earth-engine/datasets/modis-all/probe", headers=api.headers)
    assert unknown.status_code == 404 and unknown.json()["code"] == "EE_UNKNOWN_DATASET"
    ok = api.client.post("/api/v1/earth-engine/datasets/landsat-8-c2-l2/probe", headers=api.headers)
    assert ok.status_code == 200 and ok.json()["status"] == "available" and ok.json()["live"] is True
    listing = api.client.get("/api/v1/earth-engine/datasets", headers=api.headers).json()["items"]
    assert {i["slug"]: i["probe"]["status"] for i in listing}["landsat-8-c2-l2"] == "available"


def test_no_earth_engine_route_touches_the_database_beyond_identifying_the_profile(api):
    api.state.settings = make_settings(
        api.state.settings.earth_engine_control_dir.parent, earth_engine_developers="Test Reviewer"
    )
    before = db_counts()
    for call in (
        lambda: api.client.get("/api/v1/earth-engine/status", headers=api.headers),
        lambda: api.client.get("/api/v1/earth-engine/datasets", headers=api.headers),
        lambda: api.client.post("/api/v1/earth-engine/connection-test", headers=api.headers),
        lambda: api.client.post("/api/v1/earth-engine/datasets/sentinel-1-grd/probe", headers=api.headers),
    ):
        assert call().status_code == 200
    assert db_counts() == before


# --- the prototype's old route is closed -------------------------------------------------------------------------------


def test_the_prototype_provider_cannot_reach_google_during_gee_1(tmp_path, control):
    settings = make_settings(tmp_path)
    spy = mock.Mock(side_effect=AssertionError("earth engine was imported"))
    with mock.patch("importlib.import_module", spy), no_network(), pytest.raises(EarthEngineRefused) as caught:
        EarthEngineProvider.from_settings(settings, ee_module=FakeEE())
    assert caught.value.code == "EE_PHASE_NOT_ENABLED"
    spy.assert_not_called()


def test_the_prototype_provider_obeys_the_kill_switch_and_the_instance_lock_first(tmp_path, control):
    settings = make_settings(tmp_path)
    killswitch.engage(control, "t", "x")
    with pytest.raises(EarthEngineRefused) as caught:
        EarthEngineProvider.from_settings(settings)
    assert caught.value.code == "EE_KILL_SWITCH"
    with pytest.raises(ProviderUnavailable, match="PROJECT"):
        EarthEngineProvider.from_settings(make_settings(tmp_path, earth_engine_project=None))
