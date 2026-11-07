"""A machine-written DRAFT summary of the findings report, written by a local Qwen model through Ollama.

The split follows decision D-012: every fact (counts, classes, review states, where a camera was) is put together by
code from the stored report; the model only turns that JSON into sentences. Nothing is stored: the draft is
returned to the caller and is gone when the page closes. The model is reached only on this computer (a loopback
address), and the text it returns is checked before anyone sees it: a number that is not in the facts, a photograph
that is not in the facts, or any language about targeting, engagement or action causes it to be discarded.

This module describes what was detected. It never says what an object is used for, never ranks or recommends anything,
and never gives a position for a detected object (a photograph's position is where a person says the camera was).
"""

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from geopoc.config import get_settings
from geopoc.reporting import FindingsReport

MAX_PHOTOS = 12
MAX_CLASSES_PER_PHOTO = 8
MAX_ATTEMPTS = 3
OUTCOME_STATUSES = ("verified", "rejected", "inconclusive", "disputed")


def _outcomes(counts: Any) -> dict[str, int]:
    """What reviewers decided: only the decisions that exist, so 'reviewed' is never mistaken for 'verified'."""
    return {status: int(counts[status]) for status in OUTCOME_STATUSES if counts.get(status, 0) > 0}


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

DISCLAIMER = (
    "Written by a language model on this computer from the stored data in this report. It is a draft: no person has "
    "reviewed it, it can contain mistakes, and it is not a finding. Check it against the report sections."
)

SYSTEM_PROMPT = (
    "You write a short, plain-language DRAFT summary of a photograph-analysis project for a human reader, using "
    "ONLY the JSON facts you are given.\n"
    "Rules:\n"
    "- Describe what the software detected and what is recorded. Every detection is an unreviewed proposal by a model "
    "unless the facts say it was reviewed.\n"
    "- review_outcomes lists what people decided about detections they have already reviewed (for example rejected or "
    "inconclusive). Use exactly those words and counts. If it is empty, no detection has been reviewed yet.\n"
    "- Say plainly how many detections are still waiting for a person to review them, and how many came from "
    "photographs and how many from video frames.\n"
    "- A photograph's position is where a person says the CAMERA was. Never give a position for a detected object.\n"
    "- Never say what an object is used for, what it means, how important it is, or what should be done. Never "
    "recommend, rank or prioritise anything. Never mention targeting, engagement or any action against anything.\n"
    "- Do not invent numbers, places, dates, names or file names. Copy numbers and file names exactly as they are "
    "in the facts. If there are no detections, say that.\n"
    "- Overview: at most 90 words. Each photograph: at most 45 words. Notes: at most 3 short limits the reader should "
    "keep in mind.\n"
    "Answer only with JSON in the requested shape."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"filename": {"type": "string"}, "text": {"type": "string"}},
                "required": ["filename", "text"],
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "photos", "notes"],
}

# Language this feature must never produce. A draft containing any of it is discarded, not shown.
FORBIDDEN = re.compile(
    r"\b(engag\w*|strik\w*|attack\w*|destroy\w*|neutrali[sz]\w*|eliminat\w*|prioriti[sz]\w*|high[- ]value|"
    r"hostile|enemy|"
    r"weapon\w*|munition\w*|ordnance|bomb\w*|kill\w*|target\w*|"
    r"recommend\w*|course of action|threat\w*)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")


class SummaryPhoto(BaseModel):
    filename: str
    text: str


class ReportSummaryDraft(BaseModel):
    """A draft written by a model. Never stored; never a finding."""

    machine_written: bool = True
    disclaimer: str = DISCLAIMER
    model: str
    generated_at: datetime
    attempts: int
    overview: str
    photos: list[SummaryPhoto]
    notes: list[str]
    facts_used: dict[str, Any] = Field(description="The exact facts the model was given, so the draft can be checked.")


class LlmStatus(BaseModel):
    enabled: bool
    reachable: bool
    model: str
    model_installed: bool
    note: str


class LlmError(Exception):
    """The model could not be used, or what it wrote was refused. `code` is the stable diagnostic code."""

    def __init__(self, code: str, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _loopback(host: str) -> bool:
    try:
        return (urlparse(host).hostname or "") in LOOPBACK_HOSTS
    except ValueError:
        return False


def status() -> LlmStatus:
    """Whether the configured local model can be used. Reads Ollama's model list; loads nothing."""
    settings = get_settings()
    model = settings.llm_model
    if not settings.llm_enabled:
        return LlmStatus(
            enabled=False,
            reachable=False,
            model=model,
            model_installed=False,
            note="Switched off (GEOPOC_LLM_ENABLED).",
        )
    if not _loopback(settings.llm_host):
        return LlmStatus(
            enabled=True,
            reachable=False,
            model=model,
            model_installed=False,
            note="GEOPOC_LLM_HOST is not a loopback address. The model must run on this computer, so it is not used.",
        )
    try:
        response = httpx.get(f"{settings.llm_host.rstrip('/')}/api/tags", timeout=3.0)
        response.raise_for_status()
        names = {str(item.get("name")) for item in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return LlmStatus(
            enabled=True,
            reachable=False,
            model=model,
            model_installed=False,
            note="Ollama is not reachable on this computer.",
        )
    installed = model in names or f"{model}:latest" in names
    note = "Ready." if installed else f"Ollama is running but the model {model} is not installed (ollama pull {model})."
    return LlmStatus(enabled=True, reachable=True, model=model, model_installed=installed, note=note)


def build_facts(report: FindingsReport) -> dict[str, Any]:
    """Every fact the draft may use. Plain code: counts and labels straight from the stored report."""
    s = report.summary
    by_asset: dict[str, list] = {}
    for observation in report.observations:
        by_asset.setdefault(str(observation.asset_id), []).append(observation)

    photos = []
    for photo in sorted(report.photo_locations, key=lambda p: (-p.observation_count, p.filename)):
        observations = by_asset.get(str(photo.asset_id), [])
        classes = Counter(o.current_category for o in observations)
        statuses = Counter(o.review_status for o in observations)
        entry: dict[str, Any] = {
            "filename": photo.filename,
            "detections_total": len(observations),
            "detections_by_class": dict(classes.most_common(MAX_CLASSES_PER_PHOTO)),
            "classes_not_listed": max(0, len(classes) - MAX_CLASSES_PER_PHOTO),
            "detections_waiting_for_review": sum(1 for o in observations if o.review_group == "unreviewed"),
            "review_outcomes": _outcomes(statuses),
            "camera_position": "not recorded",
        }
        if photo.state == "confirmed" and photo.confirmed is not None:
            entry["camera_position"] = (
                f"confirmed by a person (source: {photo.confirmed.source_label}); "
                f"latitude {photo.confirmed.latitude:.4f}, longitude {photo.confirmed.longitude:.4f}"
            )
        elif photo.state == "unconfirmed_candidate":
            entry["camera_position"] = "a GPS position was read from the file but no person has confirmed it"
        photos.append(entry)

    modality_of = {str(a.asset_id): a.modality for a in report.assets}
    in_photographs = sum(1 for o in report.observations if modality_of.get(str(o.asset_id)) == "ground_image")
    in_video_frames = sum(1 for o in report.observations if modality_of.get(str(o.asset_id)) == "video")
    models = sorted({f"{run.model_name} {run.model_version}" for run in report.runs})
    return {
        "project_name": report.project.name,
        "files_stored": s.assets_total,
        "analysis_runs": s.runs_total,
        "models_used": models,
        "detections_total": s.observations_total,
        "detections_in_photographs": in_photographs,
        "detections_in_video_frames": in_video_frames,
        "videos_stored": sum(1 for kind in modality_of.values() if kind == "video"),
        "detections_waiting_for_review": s.observations_unreviewed,
        "review_outcomes": _outcomes(s.observations_by_review_status),
        "scores_are_calibrated_probabilities": s.scores_calibrated,
        "photographs_total": s.photos_total,
        "photographs_with_confirmed_camera_position": s.photos_confirmed_location,
        "photographs_with_unconfirmed_gps": s.photos_unconfirmed_candidate,
        "photographs_without_position": s.photos_without_location,
        "open_conflicts": s.conflicts_open,
        "photographs_listed_below": min(len(photos), MAX_PHOTOS),
        "photographs_not_listed": max(0, len(photos) - MAX_PHOTOS),
        "photographs": photos[:MAX_PHOTOS],
    }


def _numbers_in(value: Any) -> set[float]:
    found: set[float] = set()
    for token in NUMBER.findall(json.dumps(value)):
        found.add(float(token))
    return found


def _strip(text: str, names: list[str]) -> str:
    """Known file names are removed before numbers are checked: a name's digits are copied, not claimed."""
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, " ")
    return text


def check_draft(raw: dict[str, Any], facts: dict[str, Any]) -> tuple[str, list[SummaryPhoto], list[str]]:
    """Return the cleaned draft, or raise LlmError saying why it is refused."""
    overview = str(raw.get("overview", "")).strip()
    photos_raw = raw.get("photos", [])
    notes_raw = raw.get("notes", [])
    if not overview or not isinstance(photos_raw, list) or not isinstance(notes_raw, list):
        raise LlmError("LLM_OUTPUT_REJECTED", "The model's answer was not in the expected shape.", 502)
    known = {p["filename"] for p in facts["photographs"]}
    photos: list[SummaryPhoto] = []
    for item in photos_raw:
        name, text = str(item.get("filename", "")), str(item.get("text", "")).strip()
        if name not in known:
            raise LlmError("LLM_OUTPUT_REJECTED", "The draft named a photograph that is not in the stored data.", 502)
        photos.append(SummaryPhoto(filename=name, text=text))
    notes = [str(n).strip() for n in notes_raw if str(n).strip()][:3]

    everything = " ".join([overview, *(p.text for p in photos), *notes])
    if FORBIDDEN.search(everything):
        raise LlmError(
            "LLM_OUTPUT_REJECTED", "The draft used language this feature must not produce and was discarded.", 502
        )
    # "reviewed" is not "verified": a review-status word may appear only if some detection actually has that status.
    present = {status for status, n in facts.get("review_outcomes", {}).items() if n > 0}
    for status_word in ("verified", "rejected", "inconclusive", "disputed"):
        if re.search(rf"\b{status_word}\b", everything, re.IGNORECASE) and status_word not in present:
            raise LlmError(
                "LLM_OUTPUT_REJECTED",
                f"The draft said '{status_word}', but no detection has that review status in the stored data.",
                502,
            )
    allowed = _numbers_in(facts)
    for token in NUMBER.findall(_strip(everything, sorted(known))):
        value = float(token)
        if not any(abs(value - fact) < 0.00006 for fact in allowed):
            raise LlmError(
                "LLM_OUTPUT_REJECTED", f"The draft contained the number {token}, which is not in the stored data.", 502
            )
    return overview, photos, notes


def _chat(facts: dict[str, Any], reminder: str | None) -> dict[str, Any]:
    settings = get_settings()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + (f"\n{reminder}" if reminder else "")},
        {"role": "user", "content": "FACTS (JSON):\n" + json.dumps(facts, indent=1)},
    ]
    try:
        response = httpx.post(
            f"{settings.llm_host.rstrip('/')}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "stream": False,
                "format": SCHEMA,
                "options": {"temperature": 0, "num_ctx": 6144, "num_predict": 900},
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
        content = response.json()["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, TypeError, ValueError) as exc:
        raise LlmError("LLM_OUTPUT_REJECTED", "The model's answer could not be read as JSON.", 502) from exc
    if not isinstance(parsed, dict):
        raise LlmError("LLM_OUTPUT_REJECTED", "The model's answer was not a JSON object.", 502)
    return parsed


def write_draft(report: FindingsReport) -> ReportSummaryDraft:
    """Ask the local model for a draft summary of this report and check it. Raises LlmError when it cannot be used."""
    settings = get_settings()
    if not settings.llm_enabled:
        raise LlmError("LLM_DISABLED", "The draft summary is switched off (GEOPOC_LLM_ENABLED).", 409)
    if not _loopback(settings.llm_host):
        raise LlmError(
            "LLM_HOST_NOT_LOCAL", "GEOPOC_LLM_HOST is not a loopback address. The model must run on this computer.", 409
        )
    facts = build_facts(report)
    reminder: str | None = None
    last: LlmError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            overview, photos, notes = check_draft(_chat(facts, reminder), facts)
        except LlmError as error:
            if error.code != "LLM_OUTPUT_REJECTED":
                raise
            last = error
            reminder = (
                f"Your previous answer was refused: {error.message} Use only numbers and file names from the facts."
            )
            continue
        return ReportSummaryDraft(
            model=settings.llm_model,
            generated_at=datetime.now(UTC),
            attempts=attempt,
            overview=overview,
            photos=photos,
            notes=notes,
            facts_used=facts,
        )
    assert last is not None
    raise last
