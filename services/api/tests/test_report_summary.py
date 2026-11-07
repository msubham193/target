"""The draft summary of the findings report: facts come from code, a local model only words them, and the result is checked.

No model is run: `httpx.post` and `httpx.get` are replaced, and the settings are replaced with plain objects. This says
nothing about how well Qwen writes. It shows what is sent, what is refused, that the draft is never stored, and that it
cannot be used to recommend or rank anything.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from geopoc import report_summary
from geopoc.report_summary import LlmError, check_draft
from tests.test_assets import rows
from tests.test_findings_report import analysed_photo, report


def settings(**over):
    base = dict(llm_enabled=True, llm_host="http://127.0.0.1:11434", llm_model="qwen2.5vl:latest", llm_timeout_s=5.0)
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(report_summary, "get_settings", lambda: settings())


class FakeOllama:
    """Replaces httpx.post for /api/chat. `answers` are returned in turn; every request body is kept."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[dict] = []

    def __call__(self, url, json=None, timeout=None, **_):  # noqa: A002 - httpx's own parameter name
        self.requests.append({"url": url, "json": json})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        body = (
            answer if isinstance(answer, dict) and "message" in answer else {"message": {"content": json_dumps(answer)}}
        )
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))


def json_dumps(value):
    return json.dumps(value)


def good_for(facts) -> dict:
    """A draft that uses only what the facts say."""
    first = facts["photographs"][0]
    return {
        "overview": (
            f"{facts['detections_total']} detections were stored; {facts['detections_waiting_for_review']} are waiting for "
            "a person to review them. They are proposals by a model, not confirmed findings."
        ),
        "photos": [
            {
                "filename": first["filename"],
                "text": f"{first['detections_total']} detections were marked in this photograph. Camera position: {first['camera_position']}.",
            }
        ],
        "notes": ["Scores are the model's own values, not probabilities."],
    }


FACTS = {
    "detections_total": 55,
    "files_stored": 2,
    "photographs": [
        {
            "filename": "20261030_123741PMByGPS.jpg",
            "detections_total": 12,
            "camera_position": "latitude 20.2997, longitude 85.8624",
        }
    ],
}


# --- the checks on what the model wrote -----------------------------------------------------------------------------


def test_a_draft_that_only_uses_the_facts_is_accepted():
    overview, photos, notes = check_draft(
        {
            "overview": "55 detections in 2 files. 20261030_123741PMByGPS.jpg has 12, at latitude 20.2997.",
            "photos": [{"filename": "20261030_123741PMByGPS.jpg", "text": "12 detections."}],
            "notes": [],
        },
        FACTS,
    )
    assert overview.startswith("55 detections") and photos[0].text == "12 detections." and notes == []


def test_an_invented_number_discards_the_draft():
    with pytest.raises(LlmError) as error:
        check_draft({"overview": "56 detections were stored.", "photos": [], "notes": []}, FACTS)
    assert error.value.code == "LLM_OUTPUT_REJECTED" and "56" in error.value.message


def test_a_review_status_that_no_detection_has_discards_the_draft():
    facts = {**FACTS, "review_outcomes": {"rejected": 2, "inconclusive": 1}}
    # "reviewed" is not "verified": nothing here is verified, so saying so is wrong
    with pytest.raises(LlmError) as error:
        check_draft({"overview": "55 detections; 3 are verified.", "photos": [], "notes": []}, facts)
    assert "verified" in error.value.message
    check_draft(
        {"overview": "55 detections; 2 rejected, 1 inconclusive, the rest unverified.", "photos": [], "notes": []}, facts
    )


def test_a_photograph_that_is_not_in_the_facts_discards_the_draft():
    with pytest.raises(LlmError):
        check_draft(
            {"overview": "55 detections.", "photos": [{"filename": "invented.jpg", "text": "x"}], "notes": []}, FACTS
        )


@pytest.mark.parametrize(
    "sentence",
    [
        "The truck should be engaged first.",
        "We recommend monitoring this area.",
        "This is the highest priority: prioritise the two trucks.",
        "Possible hostile vehicles were detected.",
        "A weapon may be present.",
        "The target is the white van.",
        "Strike the convoy.",
    ],
)
def test_language_about_targeting_or_action_discards_the_draft(sentence):
    with pytest.raises(LlmError) as error:
        check_draft({"overview": f"55 detections. {sentence}", "photos": [], "notes": []}, FACTS)
    assert error.value.code == "LLM_OUTPUT_REJECTED"


def test_ordinary_description_is_not_mistaken_for_forbidden_language():
    check_draft(
        {"overview": "55 detections of cars and persons, all waiting for review.", "photos": [], "notes": []}, FACTS
    )
    check_draft(
        {"overview": "55 detections. Few classes were found, and none are confirmed.", "photos": [], "notes": []}, FACTS
    )
    # the word is refused in any form, including plurals and "targeting"
    for word in ("targets", "targeting", "Target"):
        with pytest.raises(LlmError):
            check_draft({"overview": f"55 detections; {word} were seen.", "photos": [], "notes": []}, FACTS)


# --- the facts are counted by code, from the stored report -----------------------------------------------------------


def test_the_facts_are_counted_from_the_stored_report(client, project, headers, run_jobs):
    analysed_photo(client, project, headers, run_jobs, name="harbour.jpg")
    facts = report_summary.build_facts(
        __import__("geopoc.reporting", fromlist=["x"]).FindingsReport(**report(client, project, headers))
    )
    assert facts["project_name"] == project["name"]
    assert facts["photographs_total"] == 1 and facts["photographs"][0]["filename"] == "harbour.jpg"
    assert facts["detections_total"] == facts["photographs"][0]["detections_total"]
    assert facts["detections_in_photographs"] == facts["detections_total"] and facts["detections_in_video_frames"] == 0
    assert facts["photographs"][0]["camera_position"] == "not recorded"
    # nothing has been reviewed yet, so every detection is waiting and no review outcome is claimed
    assert facts["detections_waiting_for_review"] == facts["detections_total"] and facts["review_outcomes"] == {}
    assert "detections_reviewed" not in facts and "detections_by_review_status" not in facts
    # nothing but counts, class names, file names and a camera position goes in: no image data, no object positions
    flat = json.dumps(facts)
    assert "region" not in flat and "pixel" not in flat.lower()


# --- the endpoint -----------------------------------------------------------------------------------------------------


def summary(client, project, headers):
    return client.post(f"/api/v1/projects/{project['id']}/report/summary", headers=headers)


def prepared(client, project, headers, run_jobs):
    analysed_photo(client, project, headers, run_jobs, name="harbour.jpg")
    return report_summary.build_facts(
        __import__("geopoc.reporting", fromlist=["x"]).FindingsReport(**report(client, project, headers))
    )


def test_a_good_draft_is_returned_marked_machine_written_and_is_never_stored(
    client, project, headers, run_jobs, local, monkeypatch
):
    facts = prepared(client, project, headers, run_jobs)
    fake = FakeOllama(good_for(facts))
    monkeypatch.setattr(httpx, "post", fake)
    response = summary(client, project, headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["machine_written"] is True and "draft" in body["disclaimer"].lower()
    assert body["model"] == "qwen2.5vl:latest" and body["attempts"] == 1
    assert body["facts_used"]["photographs"][0]["filename"] == "harbour.jpg"
    assert response.headers["cache-control"] == "no-store"

    request = fake.requests[0]
    assert request["url"] == "http://127.0.0.1:11434/api/chat"
    assert request["json"]["model"] == "qwen2.5vl:latest" and request["json"]["stream"] is False
    assert request["json"]["format"]["required"] == ["overview", "photos", "notes"]
    assert request["json"]["options"]["temperature"] == 0
    # only text facts are sent: no images
    assert all("images" not in message for message in request["json"]["messages"])

    audit = rows("SELECT details::text FROM audit_event WHERE action = 'report.summary.generate'")
    assert len(audit) == 1
    assert body["overview"] not in audit[0][0] and "harbour.jpg" not in audit[0][0]  # the text itself is not kept


def test_a_refused_first_answer_gets_one_more_try(client, project, headers, run_jobs, local, monkeypatch):
    facts = prepared(client, project, headers, run_jobs)
    bad = {"overview": "999 detections.", "photos": [], "notes": []}
    fake = FakeOllama(bad, good_for(facts))
    monkeypatch.setattr(httpx, "post", fake)
    response = summary(client, project, headers)
    assert response.status_code == 200 and response.json()["attempts"] == 2
    assert "refused" in fake.requests[1]["json"]["messages"][0]["content"]


def test_two_refused_answers_give_no_draft_at_all(client, project, headers, run_jobs, local, monkeypatch):
    prepared(client, project, headers, run_jobs)
    monkeypatch.setattr(
        httpx, "post", FakeOllama({"overview": "The truck should be engaged.", "photos": [], "notes": []})
    )
    response = summary(client, project, headers)
    assert response.status_code == 502 and response.json()["code"] == "LLM_OUTPUT_REJECTED"
    assert "overview" not in response.json()
    assert rows("SELECT count(*) FROM audit_event WHERE action = 'report.summary.refused'")[0][0] == 1


def test_it_says_so_when_the_model_is_not_reachable_or_not_installed(
    client, project, headers, run_jobs, local, monkeypatch
):
    prepared(client, project, headers, run_jobs)
    monkeypatch.setattr(httpx, "post", FakeOllama(httpx.ConnectError("refused")))
    response = summary(client, project, headers)
    assert response.status_code == 503 and response.json()["code"] == "LLM_UNAVAILABLE"

    missing = httpx.Response(404, json={"error": "model not found"}, request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(httpx, "post", FakeOllama({"message": None}) if False else (lambda *a, **k: missing))
    response = summary(client, project, headers)
    assert response.status_code == 503 and "not installed" in response.json()["title"]

    monkeypatch.setattr(httpx, "post", FakeOllama(httpx.ReadTimeout("slow")))
    assert summary(client, project, headers).status_code == 504


def test_it_can_be_switched_off_and_will_not_talk_to_a_model_elsewhere(client, project, headers, run_jobs, monkeypatch):
    prepared(client, project, headers, run_jobs)
    called = FakeOllama({"overview": "x", "photos": [], "notes": []})
    monkeypatch.setattr(httpx, "post", called)

    monkeypatch.setattr(report_summary, "get_settings", lambda: settings(llm_enabled=False))
    response = summary(client, project, headers)
    assert response.status_code == 409 and response.json()["code"] == "LLM_DISABLED"

    monkeypatch.setattr(report_summary, "get_settings", lambda: settings(llm_host="https://api.example.com"))
    response = summary(client, project, headers)
    assert response.status_code == 409 and response.json()["code"] == "LLM_HOST_NOT_LOCAL"
    assert called.requests == []  # nothing was sent anywhere


def test_it_needs_a_profile_and_a_real_project(client, project, headers):
    assert client.post(f"/api/v1/projects/{project['id']}/report/summary").status_code == 401
    missing = client.post("/api/v1/projects/00000000-0000-0000-0000-000000000000/report/summary", headers=headers)
    assert missing.status_code == 404


def test_the_status_check_reads_the_model_list_and_loads_nothing(client, monkeypatch):
    monkeypatch.setattr(report_summary, "get_settings", lambda: settings())
    tags = httpx.Response(
        200, json={"models": [{"name": "qwen2.5vl:latest"}]}, request=httpx.Request("GET", "http://x")
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: tags)
    body = client.get("/api/v1/report-summary/status").json()
    assert body == {
        "enabled": True,
        "reachable": True,
        "model": "qwen2.5vl:latest",
        "model_installed": True,
        "note": "Ready.",
    }

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    body = client.get("/api/v1/report-summary/status").json()
    assert body["reachable"] is False and "not reachable" in body["note"]
