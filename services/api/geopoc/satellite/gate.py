"""The switches that keep Earth Engine off, and the redaction that keeps credentials out of messages."""

import re

from geopoc.config import Settings


class ProviderError(RuntimeError):
    """A provider failed. The message has been redacted: it never carries a token, key or signed URL."""


class EarthEngineDisabled(ProviderError):
    """Earth Engine was asked for while it is switched off. Nothing was imported and nothing was sent."""


class ProviderUnavailable(ProviderError):
    """Earth Engine is switched on but cannot be used (package, project or credentials missing)."""


def ensure_enabled(settings: Settings) -> None:
    """Both switches are required: the global online switch and the Earth Engine one. Neither is on by default."""
    missing = [
        name
        for name, on in (
            ("GEOPOC_ONLINE_ENABLED", settings.online_enabled),
            ("GEOPOC_EARTH_ENGINE_ENABLED", settings.earth_engine_enabled),
        )
        if not on
    ]
    if missing:
        raise EarthEngineDisabled(
            f"Earth Engine is disabled ({', '.join(missing)} is not true). It contacts a Google service and needs the "
            "account owner's approval; nothing was sent."
        )


_PATTERNS = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "[private key removed]",
    ),
    (re.compile(r'("?private_key(?:_id)?"?\s*[:=]\s*)"[^"]*"'), r'\1"[removed]"'),
    (
        re.compile(
            r'("?(?:client_secret|refresh_token|access_token|id_token|token)"?\s*[:=]\s*)"?[^\s",}&]+"?', re.IGNORECASE
        ),
        r"\1[removed]",
    ),
    (re.compile(r"ya29\.[\w\-.]+"), "[token removed]"),
    (re.compile(r"1//[\w\-]{20,}"), "[token removed]"),
    (re.compile(r"eyJ[\w\-]+\.[\w\-]+\.[\w\-]+"), "[token removed]"),
    (re.compile(r"Bearer\s+[\w\-.~+/]+=*", re.IGNORECASE), "Bearer [removed]"),
    # Earth Engine download links are signed: whoever holds the link can fetch the data.
    (re.compile(r"https://[\w.\-]*(?:earthengine|googleapis)\.[\w./:\-?=&%~+]*"), "[signed url removed]"),
]


def redact(text: object) -> str:
    """The text with anything that looks like a credential removed. Applied to every message that leaves a provider."""
    out = str(text)
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out
