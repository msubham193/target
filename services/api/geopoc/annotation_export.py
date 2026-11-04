"""Map annotations as the findings report and the JSON and GeoJSON exports present them.

One read of the project's annotation table feeds all three, so they cannot disagree. Everything here only reads, and
every query is limited to one project. Annotations are never mixed into observations: the report has its own
section, the JSON export its own member, and GeoJSON features carry `feature_type: "user_annotation"`. The CSV
export has no annotation rows at all.

A deleted annotation is always labelled deleted. It is listed (with its history) in the report and the JSON export,
and it is never written as a GeoJSON feature, because a GIS would draw it like any other feature. The GeoJSON file
counts them.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from geopoc.api.routers.annotations import _out
from geopoc.api.schemas import AnnotationOut, AnnotationVersionOut
from geopoc.db.models import MapAnnotation
from geopoc.geo import annotations as service

NOT_MIGRATED = (
    "This database has not been updated for map annotations, so none can be listed. That does not mean the project has "
    "none."
)
GEOJSON_REASON = (
    "Only active annotations are written as features. A deleted annotation is not drawn or exported as a feature; "
    "it is counted here and listed with its history, marked deleted, in the JSON export and the findings report."
)


@dataclass
class Collected:
    available: bool
    items: list[AnnotationOut]  # every annotation, active and deleted, oldest first
    versions: dict[uuid.UUID, list[MapAnnotation]]

    @property
    def active(self) -> int | None:
        return sum(i.status == "active" for i in self.items) if self.available else None

    @property
    def deleted(self) -> int | None:
        return sum(i.status == "deleted" for i in self.items) if self.available else None

    @property
    def note(self) -> str:
        return service.NOTE if self.available else NOT_MIGRATED


def collect(session: Session, project_id: uuid.UUID) -> Collected:
    """The project's annotations as they are now, plus every stored version. Unmigrated database: available is False."""
    if not service.table_exists(session):
        return Collected(available=False, items=[], versions={})
    versions = service.all_versions(session, project_id)
    ordered = sorted(versions.values(), key=lambda rows: (rows[0].created_at, str(rows[0].annotation_id)))
    return Collected(available=True, items=[_out(rows[-1], rows[0]) for rows in ordered], versions=versions)


def version_out(row: MapAnnotation) -> AnnotationVersionOut:
    return AnnotationVersionOut(
        version_no=row.version_no,
        action=row.action,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        geometry=service.geometry_of(row),
        note=row.note,
        reason=row.reason,
        actor_label=row.actor_label,
        created_at=row.created_at,
    )


def geometry_changed(rows: list[MapAnnotation]) -> list[bool]:
    """For each version: did its shape differ from the version before it (the first version created the shape)."""
    out = []
    for index, row in enumerate(rows):
        out.append(index == 0 or service.geometry_of(row) != service.geometry_of(rows[index - 1]))
    return out


def json_section(collected: Collected) -> dict[str, Any]:
    """The `annotations` member of the JSON export: current state of every annotation, with its full version history."""
    return {
        "available": collected.available,
        "note": collected.note,
        "active": collected.active,
        "deleted": collected.deleted,
        "items": [
            {
                **item.model_dump(mode="json"),
                "versions": [version_out(r).model_dump(mode="json") for r in collected.versions[item.annotation_id]],
            }
            for item in collected.items
        ],
    }


def geojson_features(collected: Collected) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """GeoJSON features for the active annotations, and the foreign member that says what was and was not written."""
    features = [
        {
            "type": "Feature",
            "id": f"annotation:{item.annotation_id}",
            "geometry": item.geometry,
            "properties": {
                "feature_type": "user_annotation",
                "annotation_id": str(item.annotation_id),
                "kind": item.kind,
                "status": item.status,
                "note": item.note,
                "version_no": item.version_no,
                "crs_id": item.crs_id,
                "created_by": item.created_by,
                "created_at": item.created_at.isoformat(),
                "updated_by": item.updated_by,
                "updated_at": item.updated_at.isoformat(),
            },
        }
        for item in collected.items
        if item.status == "active"
    ]
    summary = {
        "available": collected.available,
        "exported": len(features),
        "deleted_not_exported": collected.deleted,
        "note": collected.note,
        "reason": GEOJSON_REASON,
    }
    return features, summary
