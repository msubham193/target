"""A machine-written DRAFT description of what a ground photograph shows, by the local Qwen vision model through Ollama.

The split follows decision D-012, as in `geopoc.report_summary`: the facts (what the detector found in the latest
analysis, counted by class) are put together by code from the stored records; the model looks at the photograph's
preview picture and describes the scene in words: what kind of place it is and what is going on. Nothing is stored:
the draft is returned and is gone when the page closes. The model is reached only on this computer, and what it writes
is checked before anyone sees it: a number that is not in the facts, or any language about targeting, engagement,
threat or intent, causes it to be discarded.

It describes what is visible. It never says what anyone intends, never ranks or recommends anything, and never gives a
position for anything in the picture (a photograph's position is where a person says the camera was).
"""

import base64
import io
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from geopoc.config import get_settings
from geopoc.db.models import Artifact, Asset, ObservationRecord
from geopoc.report_summary import FORBIDDEN, NUMBER, LlmError, _loopback
from geopoc.storage import store

MAX_ATTEMPTS = 3
MAX_CLASSES = 12
# The picture sent to the model: the stored preview, made smaller. Enough to see a street scene, quick to read.
IMAGE_LONGEST_PX = 1024

DISCLAIMER = (
    "Written by a vision-language model on this computer from the photograph and the detector's counts. It is a draft: "
    "no person has reviewed it, it can be wrong, and it is not a finding."
)

SYSTEM_PROMPT = (
    "You look at one ground-level photograph and write a short, plain DRAFT description of the scene for a human "
    "reader. You are also given JSON facts: what an object detector found in this photograph, counted by class.\n"
    "Rules:\n"
    "- Describe only what is visible: the kind of place (for example a road, a junction, a market, a residential "
    "street), the road layout, buildings and shops, traffic and people, and what is going on (vehicles moving or "
    "parked, people walking or waiting), the light and the weather.\n"
    "- Use the detector's counts for numbers of objects, exactly as given. For anything else write quantities in words "
    "(several, many, a few), never as digits. Do not copy any text, numbers, dates or coordinates printed on the "
    "photo; you may say that a text stamp is present.\n"
    "- Never say what anyone intends, plans or is about to do, and never say what something is used for, how important "
    "it is, or what should be done. Never recommend, rank or prioritise. Never mention targeting, engagement, threats, "
    "weapons or any action against anything. Never give a position or distance for anything in the picture.\n"
    "- scene: at most 45 words. activity: at most 60 words. details: at most 4 short points (each at most 20 words). "
    "detector_check: one sentence on whether the detector's counts look consistent with the picture (for example if "
    "something visible seems missing, or a box seems to be on a printed stamp), in at most 30 words.\n"
    "Answer only with JSON in the requested shape."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "activity": {"type": "string"},
        "details": {"type": "array", "items": {"type": "string"}},
        "detector_check": {"type": "string"},
    },
    "required": ["scene", "activity", "details", "detector_check"],
}


class SceneDescriptionDraft(BaseModel):
    """A draft written by a model from one photograph. Never stored; never a finding."""

    machine_written: bool = True
    disclaimer: str = DISCLAIMER
    asset_id: uuid.UUID
    model: str
    generated_at: datetime
    attempts: int
    scene: str = Field(description="What kind of place the photograph shows.")
    activity: str = Field(description="What is going on in the picture.")
    details: list[str]
    detector_check: str = Field(description="Whether the detector's counts look consistent with the picture.")
    facts_used: dict[str, Any] = Field(description="The exact facts the model was given, so the draft can be checked.")


def _label(category: str) -> str:
    return category.split(":", 1)[1] if ":" in category else category


def build_facts(session: Session, asset: Asset) -> dict[str, Any]:
    """What the detector found in the photograph's latest analysis, counted by code. Rejected findings are left out."""
    latest = session.execute(
        select(ObservationRecord.run_id)
        .where(ObservationRecord.asset_id == asset.id)
        .group_by(ObservationRecord.run_id)
        .order_by(func.max(ObservationRecord.created_at).desc())
        .limit(1)
    ).scalar_one_or_none()
    facts: dict[str, Any] = {"photograph": asset.original_filename, "analysed": latest is not None}
    if latest is None:
        facts.update({"detections_total": 0, "detections_by_class": {}})
        return facts
    rows = session.execute(
        select(ObservationRecord.current_category, ObservationRecord.review_status).where(
            ObservationRecord.asset_id == asset.id, ObservationRecord.run_id == latest
        )
    ).all()
    kept = [_label(category) for category, review in rows if review != "rejected"]
    counts = Counter(kept)
    facts.update(
        {
            "detections_total": len(kept),
            "detections_by_class": dict(counts.most_common(MAX_CLASSES)),
            "classes_not_listed": max(0, len(counts) - MAX_CLASSES),
            "detections_rejected_by_a_reviewer_and_left_out": sum(1 for _, review in rows if review == "rejected"),
            "detections_are_unreviewed_model_proposals_unless_reviewed": True,
        }
    )
    return facts


def preview_jpeg(session: Session, asset: Asset) -> bytes:
    """The stored preview picture as a JPEG of at most IMAGE_LONGEST_PX. The original file is never sent."""
    artifact = session.scalars(
        select(Artifact).where(Artifact.asset_id == asset.id, Artifact.kind == "preview", Artifact.variant == "view")
    ).first()
    path = store.absolute_path(artifact.storage_path) if artifact else None
    if path is None or not path.is_file():
        raise LlmError("PREVIEW_NOT_AVAILABLE", "The photograph has no preview picture to describe yet.", 409)
    with Image.open(path) as picture:
        image = picture.convert("RGB")
    image.thumbnail((IMAGE_LONGEST_PX, IMAGE_LONGEST_PX))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


def _numbers_in(value: Any) -> set[float]:
    return {float(token) for token in NUMBER.findall(json.dumps(value))}


def check_draft(raw: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str, list[str], str]:
    """Return the cleaned draft, or raise LlmError saying why it is refused."""
    scene = str(raw.get("scene", "")).strip()
    activity = str(raw.get("activity", "")).strip()
    details_raw = raw.get("details", [])
    detector_check = str(raw.get("detector_check", "")).strip()
    if not scene or not activity or not isinstance(details_raw, list):
        raise LlmError("LLM_OUTPUT_REJECTED", "The model's answer was not in the expected shape.", 502)
    details = [str(item).strip() for item in details_raw if str(item).strip()][:4]
    everything = " ".join([scene, activity, *details, detector_check])
    if FORBIDDEN.search(everything):
        raise LlmError(
            "LLM_OUTPUT_REJECTED", "The draft used language this feature must not produce and was discarded.", 502
        )
    allowed = _numbers_in(facts)
    for token in NUMBER.findall(everything.replace(facts.get("photograph", ""), " ")):
        if not any(abs(float(token) - fact) < 1e-9 for fact in allowed):
            raise LlmError(
                "LLM_OUTPUT_REJECTED",
                f"The draft contained the number {token}, which is not in the detector's counts.",
                502,
            )
    return scene, activity, details, detector_check


def _chat(facts: dict[str, Any], picture: bytes, reminder: str | None) -> dict[str, Any]:
    settings = get_settings()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + (f"\n{reminder}" if reminder else "")},
        {
            "role": "user",
            "content": "FACTS (JSON):\n" + json.dumps(facts, indent=1) + "\nDescribe the photograph.",
            "images": [base64.b64encode(picture).decode("ascii")],
        },
    ]
    try:
        response = httpx.post(
            f"{settings.llm_host.rstrip('/')}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "stream": False,
                "format": SCHEMA,
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 700},
            },
            timeout=settings.llm_timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise LlmError("LLM_TIMEOUT", "The local model took too long to answer.", 504) from exc
    except httpx.HTTPError as exc:
        raise LlmError("LLM_UNAVAILABLE", "The local model (Ollama) is not reachable on this computer.") from exc
    if response.status_code == 404:
        raise LlmError("LLM_UNAVAILABLE", f"The model {settings.llm_model} is not installed in Ollama.")
    if response.status_code >= 400:
        raise LlmError("LLM_UNAVAILABLE", f"Ollama answered with HTTP {response.status_code}.", 502)
    try:
        parsed = json.loads(response.json()["message"]["content"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LlmError("LLM_OUTPUT_REJECTED", "The model's answer could not be read as JSON.", 502) from exc
    if not isinstance(parsed, dict):
        raise LlmError("LLM_OUTPUT_REJECTED", "The model's answer was not a JSON object.", 502)
    return parsed


def describe(session: Session, asset: Asset) -> SceneDescriptionDraft:
    """Ask the local vision model to describe this photograph, and check what it wrote. Raises LlmError otherwise."""
    settings = get_settings()
    if not settings.llm_enabled:
        raise LlmError("LLM_DISABLED", "The AI description is switched off (GEOPOC_LLM_ENABLED).", 409)
    if not _loopback(settings.llm_host):
        raise LlmError(
            "LLM_HOST_NOT_LOCAL", "GEOPOC_LLM_HOST is not a loopback address. The model must run on this computer.", 409
        )
    facts = build_facts(session, asset)
    picture = preview_jpeg(session, asset)
    reminder: str | None = None
    last: LlmError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            scene, activity, details, detector_check = check_draft(_chat(facts, picture, reminder), facts)
        except LlmError as error:
            if error.code != "LLM_OUTPUT_REJECTED":
                raise
            last = error
            reminder = (
                f"Your previous answer was refused: {error.message} Write other quantities in words, and copy no text "
                "or numbers printed on the photo."
            )
            continue
        return SceneDescriptionDraft(
            asset_id=asset.id,
            model=settings.llm_model,
            generated_at=datetime.now(UTC),
            attempts=attempt,
            scene=scene,
            activity=activity,
            details=details,
            detector_check=detector_check,
            facts_used=facts,
        )
    assert last is not None
    raise last
