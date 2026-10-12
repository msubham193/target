import base64
import uuid
from datetime import datetime

from geopoc.api.errors import ProblemError


def encode_cursor(timestamp: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{timestamp.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        timestamp, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(timestamp), uuid.UUID(row_id)
    except Exception as exc:  # any malformed cursor is the same client error
        raise ProblemError(422, "INVALID_CURSOR", "The pagination cursor is not valid") from exc
