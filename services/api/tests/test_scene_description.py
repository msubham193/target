"""The AI description of a photograph: the detector's counts come from code, a local vision model describes the picture,
and what it writes is checked.

No model is run: `httpx.post` is replaced, and the settings are replaced with plain objects. This says nothing about how
well Qwen describes a scene. It shows what is sent (the preview picture, never the original, and the counted facts), what
is refused, that nothing is stored, and that it cannot be used to recommend, rank or locate anything.
"""

import base64
import io
import uuid

import httpx
import pytest
from PIL import Image

from geopoc import scene_description
from geopoc.report_summary import LlmError
from geopoc.scene_description import check_draft
from tests import media
from tests.test_assets import rows, upload
from tests.test_findings_report import analysed_photo, snapshot
from tests.test_report_summary import FakeOllama, settings


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(scene_description, "get_settings", lambda: settings())


def describe(client, asset_id, headers):
    return client.post(f"/api/v1/assets/{asset_id}/scene-description", headers=headers)


def good_for(facts) -> dict:
    total = facts["detections_total"]
    return {
        "scene": "A busy road lined with shops under an overcast sky.",
        "activity": f"The detector marked {total} objects; several vehicles are moving and a few people are walking.",
        "details": ["Buildings of several storeys face the road.", "A text stamp is printed on the photo."],
        "detector_check": "The counts look consistent with the vehicles visible on the road.",
    }


FACTS = {"photograph": "20261030_123741PMByGPS.jpg", "detections_total": 54, "detections_by_class": {"car": 23}}


# --- checking what the model wrote -----------------------------------------------------------------------------------


def test_a_description_that_keeps_to_the_counts_is_accepted():
    scene, activity, details, check = check_draft(
        {
            "scene": "A busy road.",
            "activity": "23 cars are on the road among many other vehicles.",
            "details": ["Overcast.", "", "Shops.", "Trees.", "Wires.", "Signs."],
            "detector_check": "Consistent.",
        },
        FACTS,
    )
    assert scene == "A busy road." and "23 cars" in activity
    assert details == ["Overcast.", "Shops.", "Trees.", "Wires."]  # empty points dropped, at most four kept


@pytest.mark.parametrize(
    "text",
    [
        "There are 31 cars on the road.",  # a number not in the counts
        "The coordinates printed are 20.2997 and 85.8624.",  # copied from the stamp
        "The trucks are a likely target.",
        "This convoy could be a threat.",
        "We recommend watching the junction.",
    ],
)
def test_invented_numbers_and_forbidden_language_are_refused(text):
    with pytest.raises(LlmError) as refused:
        check_draft({"scene": "A road.", "activity": text, "details": [], "detector_check": "ok"}, FACTS)
    assert refused.value.code == "LLM_OUTPUT_REJECTED"


def test_an_answer_in_the_wrong_shape_is_refused():
    with pytest.raises(LlmError):
        check_draft({"scene": "", "activity": "x", "details": [], "detector_check": ""}, FACTS)
    with pytest.raises(LlmError):
        check_draft({"scene": "x", "activity": "x", "details": "not a list", "detector_check": ""}, FACTS)


# --- the endpoint ------------------------------------------------------------------------------------------------------


def test_the_photo_is_described_from_its_preview_and_the_counted_facts_and_nothing_is_stored(
    client, project, headers, run_jobs, local, monkeypatch
):
    asset = analysed_photo(client, project, headers, run_jobs, name="street.jpg")
    with scene_description_session() as session:
        facts = scene_description.build_facts(session, session.get(scene_description.Asset, uuid.UUID(asset["id"])))
    assert facts["analysed"] is True and facts["photograph"] == "street.jpg"
    assert facts["detections_total"] == sum(facts["detections_by_class"].values())

    fake = FakeOllama(good_for(facts))
    monkeypatch.setattr(httpx, "post", fake)
    before = snapshot()
    response = describe(client, asset["id"], headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["machine_written"] is True and "draft" in body["disclaimer"].lower()
    assert body["scene"].startswith("A busy road") and body["attempts"] == 1
    assert body["facts_used"] == facts
    assert response.headers["cache-control"] == "no-store"
    assert snapshot() == before  # nothing but the audit log is written

    request = fake.requests[0]["json"]
    assert request["model"] == "qwen2.5vl:latest" and request["options"]["temperature"] == 0
    assert request["format"]["required"] == ["scene", "activity", "details", "detector_check"]
    images = request["messages"][1]["images"]
    assert len(images) == 1
    with Image.open(io.BytesIO(base64.b64decode(images[0]))) as sent:
        assert sent.format == "JPEG" and max(sent.size) <= scene_description.IMAGE_LONGEST_PX
    # Never the original bytes.
    assert base64.b64decode(images[0]) != media.image_bytes("JPEG", size=(64, 48), seed=1)

    audit = rows("SELECT details::text FROM audit_event WHERE action = 'photo.scene_description.generate'")
    assert len(audit) == 1 and body["scene"] not in audit[0][0]  # the text itself is not kept


def test_a_refused_answer_is_tried_again_and_then_given_up(client, project, headers, run_jobs, local, monkeypatch):
    asset = analysed_photo(client, project, headers, run_jobs, name="street.jpg")
    bad = {"scene": "A road.", "activity": "It looks like a target.", "details": [], "detector_check": "ok"}
    fake = FakeOllama(bad, {"scene": "A road.", "activity": "Some traffic.", "details": [], "detector_check": "ok"})
    monkeypatch.setattr(httpx, "post", fake)
    response = describe(client, asset["id"], headers)
    assert response.status_code == 200 and response.json()["attempts"] == 2
    assert "refused" in fake.requests[1]["json"]["messages"][0]["content"]

    monkeypatch.setattr(httpx, "post", FakeOllama(bad))
    response = describe(client, asset["id"], headers)
    assert response.status_code == 502 and response.json()["code"] == "LLM_OUTPUT_REJECTED"
    assert rows("SELECT count(*) FROM audit_event WHERE action = 'photo.scene_description.refused'")[0][0] == 1


def test_it_says_so_when_the_model_cannot_be_used(client, project, headers, run_jobs, local, monkeypatch):
    asset = analysed_photo(client, project, headers, run_jobs, name="street.jpg")
    monkeypatch.setattr(httpx, "post", FakeOllama(httpx.ConnectError("refused")))
    assert describe(client, asset["id"], headers).json()["code"] == "LLM_UNAVAILABLE"
    monkeypatch.setattr(httpx, "post", FakeOllama(httpx.ReadTimeout("slow")))
    assert describe(client, asset["id"], headers).status_code == 504

    monkeypatch.setattr(scene_description, "get_settings", lambda: settings(llm_enabled=False))
    assert describe(client, asset["id"], headers).json()["code"] == "LLM_DISABLED"
    monkeypatch.setattr(scene_description, "get_settings", lambda: settings(llm_host="http://10.0.0.5:11434"))
    assert describe(client, asset["id"], headers).json()["code"] == "LLM_HOST_NOT_LOCAL"


def test_only_ground_photographs_are_described(client, project, headers, local):
    raster = upload(client, project, headers, "r.tif", media.geotiff_bytes(), "overhead_raster").json()["asset"]
    response = describe(client, raster["id"], headers)
    assert response.status_code == 409 and response.json()["code"] == "NOT_A_PHOTOGRAPH"
    assert describe(client, "00000000-0000-4000-8000-000000000000", headers).status_code == 404


def scene_description_session():
    from sqlalchemy.orm import Session

    from geopoc.db.session import get_engine

    return Session(get_engine())
