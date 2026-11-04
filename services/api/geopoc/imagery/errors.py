"""Why a catalogue search was refused or failed. Every refusal has a stable code and an HTTP status."""

import re
from collections.abc import Iterable

# code -> HTTP status. A refusal because of the feature's state (switched off, project local-only, no confirmation, not
# configured) is 409: no input can make the server fail, so 5xx is not used for these. 502/504 are kept for a real
# failure reported by, or while talking to, the catalogue.
STATUS: dict[str, int] = {
    "IMAGERY_DISABLED": 409,
    "IMAGERY_NOT_CONFIGURED": 409,
    "IMAGERY_PROJECT_LOCAL_ONLY": 409,
    "IMAGERY_PROJECT_ARCHIVED": 409,
    "IMAGERY_CONFIRMATION_REQUIRED": 409,
    "IMAGERY_UNKNOWN_PROVIDER": 422,
    "IMAGERY_UNSUPPORTED_COLLECTION": 422,
    "IMAGERY_PAGE_TOKEN_INVALID": 422,
    "AOI_INVALID": 422,
    "AOI_RANGE": 422,
    "AOI_ANTIMERIDIAN": 422,
    "AOI_TOO_LARGE": 422,
    "AOI_TOO_COMPLEX": 422,
    "DATES_INVALID": 422,
    "DATES_FUTURE": 422,
    "DATES_TOO_EARLY": 422,
    "DATES_TOO_LONG": 422,
    "CLOUD_RANGE": 422,
    "LIMIT_RANGE": 422,
    "IMAGERY_UPSTREAM_RATE_LIMITED": 429,
    "IMAGERY_UPSTREAM_REJECTED": 502,
    "IMAGERY_UPSTREAM_BAD_REQUEST": 502,
    "IMAGERY_UPSTREAM_ERROR": 502,
    "IMAGERY_UPSTREAM_UNREACHABLE": 502,
    "IMAGERY_BAD_RESPONSE": 502,
    "IMAGERY_UPSTREAM_TIMEOUT": 504,
}

_PATTERNS = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "[private key removed]",
    ),
    (re.compile(r"(?i)\bBearer\s+[\w\-.~+/]+=*"), "Bearer [removed]"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/]{8,}=*"), "Basic [removed]"),
    (
        re.compile(
            r'(?i)("?(?:authorization|access_token|refresh_token|id_token|client_secret|password|token|api[_-]?key|'
            r"secret|secret_access_key|access_key(?:_id)?|x-amz-security-token|x-amz-signature|x-amz-credential|"
            r'signature|sig)"?\s*[:=]\s*)"?[^\s",}&]+"?'
        ),
        r"\1[removed]",
    ),
    (re.compile(r"eyJ[\w\-]+\.[\w\-]+\.[\w\-]*"), "[token removed]"),
    (re.compile(r"ya29\.[\w\-.]+"), "[token removed]"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"), "[access key removed]"),
]


def redact(text: object, secrets: Iterable[str | None] = ()) -> str:
    """The text with anything that looks like a credential removed, and any exact secret value the caller knows about.

    Applied to every message that leaves the catalogue code, whether it came from this application or the provider."""
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "[removed]")
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def clean_text(value: object, limit: int = 200) -> str:
    """Provider-supplied text made safe to show: control characters dropped, length capped."""
    text = "".join(ch for ch in str(value) if ch.isprintable())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ImageryRefused(Exception):
    """A catalogue search was not made, or failed. The message is redacted on creation."""

    def __init__(self, code: str, message: str, secrets: Iterable[str | None] = ()) -> None:
        super().__init__(redact(message, secrets))
        self.code = code

    @property
    def status(self) -> int:
        return STATUS.get(self.code, 409)
