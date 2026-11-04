"""How a refused or failed coordinate conversion is reported by the API, the same way everywhere."""

from geopoc.api.errors import ProblemError
from geopoc.geo import transform as transforms

OPERATION_DESCRIPTION = (
    "The datum transformation chosen for this conversion (an id from the CRS assessment). Needed only where several "
    "registered operations apply; georeferencing recorded by a person keeps its own choice. Ballpark transformations "
    "are never used."
)


def candidates(plan: transforms.Plan | None) -> list[dict[str, str]]:
    """The operations a person can choose between (or why none can be used)."""
    if plan is None:
        return []
    return [{"field": o.id, "message": f"{o.name}: {o.summary()}"} for o in plan.operations]


def problem_for(exc: transforms.TransformationError) -> ProblemError:
    """The problem response for a conversion that may not be used, listing the candidates so it is actionable."""
    return ProblemError(
        exc.status, exc.code, exc.title, exc.detail, errors=candidates(exc.plan), context=exc.extra or None
    )
