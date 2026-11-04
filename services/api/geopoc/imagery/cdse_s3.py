"""Copernicus Data Space Ecosystem (CDSE) S3: configuration, status, and a one-request authentication check.

This phase only answers "do the configured S3 credentials work?". It lists nothing useful, reads no object, writes
nothing and is not used by search or ingestion. Nothing about a project or an area of interest is ever sent: the one
request is a signed listing of the `eodata` bucket limited to a single key under a fixed public prefix.

  * Off by default (CDSE_S3_ENABLED) and behind the application-wide GEOPOC_ONLINE_ENABLED gate.
  * `describe` makes no request. `verify` makes at most one, and none when disabled or when credentials are missing.
  * Credentials come from the environment (or the git-ignored .env), are held as secrets, and are never returned,
    logged, or placed in a message: results carry only a status and a fixed, code-selected sentence.
  * A TCP or TLS connection proves nothing. Authenticated means the store answered a signed listing with a bucket
    listing, nothing less.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from geopoc.config import LOOPBACK_HOSTS, REPO_ROOT
from geopoc.imagery.errors import redact
from geopoc.imagery.s3sign import encode_path, encode_query, sign_get

logger = logging.getLogger("geopoc.imagery")

DEFAULT_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
BUCKET = "eodata"
# A fixed, public, top-level prefix. With a delimiter and max-keys=1 the answer is a single "folder" entry.
PROBE_PREFIX = "Sentinel-2/"
MAX_BODY_BYTES = 64 * 1024
# The only objects a HEAD may name: the two metadata files at the root of a Sentinel-2 L2A .SAFE product folder (ESA
# product format). Band files and everything else live deeper (GRANULE/...), so this cannot reach image content.
PRODUCT_KEY_RE = re.compile(
    r"^Sentinel-2/MSI/L2A/\d{4}/\d{2}/\d{2}/S2[A-D]_MSIL2A_[A-Za-z0-9_]{1,120}\.SAFE/(?:MTD_MSIL2A\.xml|manifest\.safe)\Z"
)  # \Z, not $: "$" would also accept a trailing newline
USER_AGENT = "geopoc-imagery/0.1 (local research workbench)"

STATUSES = (
    "DISABLED",
    "MISSING_CREDENTIALS",
    "NOT_VERIFIED",
    "AUTHENTICATED",
    "AUTHENTICATION_FAILED",
    "CONNECTION_FAILED",
)

_ACCESS_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,128}$")
_SECRET_KEY_RE = re.compile(r"^[\x21-\x7e]{8,256}$")  # printable ASCII, no whitespace
_REGION_RE = re.compile(r"^[a-z0-9\-]{1,32}$")
_CODE_RE = re.compile(rb"<Code>([A-Za-z]{3,64})</Code>")

# What the store says (an S3 error code) -> (our status, reason). Only these exact codes are recognised; nothing the
# store writes is ever repeated in a result.
_STORE_CODES = {
    "InvalidAccessKeyId": ("AUTHENTICATION_FAILED", "invalid_access_key"),
    "SignatureDoesNotMatch": ("AUTHENTICATION_FAILED", "signature_mismatch"),
    "AccessDenied": ("AUTHENTICATION_FAILED", "access_denied"),
    "ExpiredToken": ("AUTHENTICATION_FAILED", "credentials_expired"),
    "InvalidToken": ("AUTHENTICATION_FAILED", "credentials_expired"),
    "AuthorizationHeaderMalformed": ("AUTHENTICATION_FAILED", "authorization_malformed"),
    "InvalidRequest": ("AUTHENTICATION_FAILED", "authorization_malformed"),
    "RequestTimeTooSkewed": ("CONNECTION_FAILED", "clock_skew"),
    "NoSuchBucket": ("CONNECTION_FAILED", "bucket_not_found"),
    "SlowDown": ("CONNECTION_FAILED", "rate_limited"),
}

MESSAGES = {
    "switched_off": "The CDSE S3 integration is switched off (CDSE_S3_ENABLED is not true). Nothing was sent.",
    "online_gate_closed": "Online access is switched off (GEOPOC_ONLINE_ENABLED is not true). Nothing was sent.",
    "credentials_missing": "The access key or the secret key is not set. Nothing was sent.",
    "credentials_malformed": "The access key or the secret key is not in a valid format. Nothing was sent.",
    "invalid_endpoint": "The S3 endpoint or region is not valid (an https:// host with no path is required). "
    "Nothing was sent.",
    "not_verified": "Configured. Authentication has not been checked; run the verification to check it.",
    "authenticated": "The store accepted the signed request: the credentials are valid.",
    "invalid_access_key": "The store does not recognise the access key. It may be wrong, deleted or expired.",
    "signature_mismatch": "The store rejected the signature. The secret key does not match the access key.",
    "access_denied": "The store recognised the request but refused it. The credentials may have expired.",
    "credentials_expired": "The credentials have expired. Generate new ones.",
    "authorization_malformed": "The store could not read the authorisation. Check the region and the credentials.",
    "rejected": "The store refused the credentials.",
    "clock_skew": "This computer's clock differs too much from the store's. Correct the system time and try again.",
    "bucket_not_found": "The store does not have the expected bucket. Check the endpoint.",
    "rate_limited": "The store is limiting requests. Try again later.",
    "timeout": "The store did not answer in time.",
    "unreachable": "The store could not be reached.",
    "server_error": "The store reported an internal error. Try again later.",
    "unexpected_redirect": "The store answered with a redirect, which is not followed. Check the endpoint.",
    "unexpected_response": "The answer was not an S3 bucket listing. Check the endpoint.",
}


class CdseS3Settings(BaseSettings):
    """CDSE_S3_* from the environment or the git-ignored .env. Off by default, with no credentials by default."""

    model_config = SettingsConfigDict(
        env_prefix="CDSE_S3_", env_file=REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True
    )

    enabled: bool = False
    endpoint: str = DEFAULT_ENDPOINT
    region: str = "default"
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    timeout_s: float = 10.0


@lru_cache
def get_cdse_s3_settings() -> CdseS3Settings:
    return CdseS3Settings()


@dataclass(frozen=True)
class S3Status:
    """A sanitised answer. It holds no credential and nothing the store wrote."""

    enabled: bool
    configured: bool
    authenticated: bool
    status: str
    endpoint: str
    region: str
    reason: str
    message: str
    online_gate_open: bool
    checked_at: datetime | None = None


def _clean_endpoint(endpoint: str) -> str | None:
    """The endpoint as `https://host[:port]`, or None when it is not one (a path, query, credentials or plain http)."""
    try:
        parts = urlsplit(endpoint.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    secure = parts.scheme == "https"
    local = parts.scheme == "http" and host in LOOPBACK_HOSTS  # plain http only to this machine, for local stand-ins
    if not (secure or local) or not host or parts.username or parts.password or parts.query or parts.fragment:
        return None
    if parts.path not in ("", "/"):
        return None
    shown_host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown_host}" + (f":{port}" if port else "")


def _host_header(endpoint: str) -> str:
    """The Host value the HTTP client will send: the port only when it is not the scheme's default."""
    parts = urlsplit(endpoint)
    host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname or ""
    default = 443 if parts.scheme == "https" else 80
    return host if parts.port in (None, default) else f"{host}:{parts.port}"


def _credentials(settings: CdseS3Settings) -> tuple[str, str] | None:
    access = settings.access_key.get_secret_value().strip() if settings.access_key else ""
    secret = settings.secret_key.get_secret_value().strip() if settings.secret_key else ""
    return (access, secret) if access and secret else None


def _configured(settings: CdseS3Settings) -> bool:
    """Credentials in a valid format, a valid endpoint and region. Says nothing about whether they are accepted."""
    pair = _credentials(settings)
    return bool(
        pair
        and _ACCESS_KEY_RE.match(pair[0])
        and _SECRET_KEY_RE.match(pair[1])
        and _clean_endpoint(settings.endpoint)
        and _REGION_RE.match(settings.region)
    )


def _status(
    settings: CdseS3Settings, online: bool, status: str, reason: str, checked: datetime | None = None
) -> S3Status:
    endpoint = _clean_endpoint(settings.endpoint)
    return S3Status(
        enabled=settings.enabled,
        configured=_configured(settings),
        authenticated=status == "AUTHENTICATED",
        status=status,
        endpoint=endpoint or "(invalid)",
        region=settings.region if _REGION_RE.match(settings.region) else "(invalid)",
        reason=reason,
        message=MESSAGES[reason],
        online_gate_open=online,
        checked_at=checked,
    )


def _local_problem(settings: CdseS3Settings, online: bool) -> S3Status | None:
    """Why no request can be made, from configuration alone; None when one can."""
    if not settings.enabled:
        return _status(settings, online, "DISABLED", "switched_off")
    if not online:
        return _status(settings, online, "DISABLED", "online_gate_closed")
    pair = _credentials(settings)
    if pair is None:
        return _status(settings, online, "MISSING_CREDENTIALS", "credentials_missing")
    if not _ACCESS_KEY_RE.match(pair[0]) or not _SECRET_KEY_RE.match(pair[1]):
        return _status(settings, online, "MISSING_CREDENTIALS", "credentials_malformed")
    if _clean_endpoint(settings.endpoint) is None or not _REGION_RE.match(settings.region):
        return _status(settings, online, "CONNECTION_FAILED", "invalid_endpoint")
    return None


def describe(settings: CdseS3Settings, *, online_enabled: bool) -> S3Status:
    """The state of the configuration. Makes no request, so it can never report AUTHENTICATED."""
    return _local_problem(settings, online_enabled) or _status(settings, online_enabled, "NOT_VERIFIED", "not_verified")


def request_summary(settings: CdseS3Settings) -> dict[str, object]:
    """Exactly what verification sends, for the confirmation shown before it. No credential is included."""
    endpoint = _clean_endpoint(settings.endpoint) or "(invalid)"
    return {
        "method": "GET",
        "url": f"{endpoint}{encode_path('/' + BUCKET)}?{encode_query(_probe_query())}",
        "signed_with": "AWS Signature V4; the access key id is in the request, the secret key never is",
        "body": "none",
        "sends_project_or_area_data": False,
    }


def _probe_query() -> dict[str, str]:
    return {"list-type": "2", "prefix": PROBE_PREFIX, "delimiter": "/", "max-keys": "1"}


class CdseS3Client:
    """The S3 connection to CDSE. It can make one request, the authentication probe; there is no way to read or write
    an object. Credentials are held privately and are never part of an exception message."""

    def __init__(
        self,
        settings: CdseS3Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        now: datetime | None = None,
    ) -> None:
        pair = _credentials(settings)
        endpoint = _clean_endpoint(settings.endpoint)
        if pair is None or endpoint is None:
            raise ValueError("credentials and a valid endpoint are required")  # callers check first; carries no value
        self._access, self._secret = pair
        self._endpoint = endpoint
        self._region = settings.region
        self._timeout = max(1.0, min(float(settings.timeout_s), 60.0))
        self._transport = transport
        self._now = now

    def __repr__(self) -> str:
        return f"CdseS3Client(endpoint={self._endpoint!r}, region={self._region!r})"

    def probe(self) -> tuple[str, str]:
        """(status, reason) from one signed, read-only listing of at most one key. Never raises for a network fault."""
        query = _probe_query()
        path = "/" + BUCKET
        headers = sign_get(
            method="GET",
            host=_host_header(self._endpoint),
            path=path,
            query=query,
            access_key=self._access,
            secret_key=self._secret,
            region=self._region,
            now=self._now,
        )
        headers.update({"User-Agent": USER_AGENT, "Accept": "application/xml"})
        url = f"{self._endpoint}{encode_path(path)}?{encode_query(query)}"
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=httpx.Timeout(self._timeout, connect=min(5.0, self._timeout)),
                follow_redirects=False,
            ) as http:
                with http.stream("GET", url, headers=headers) as response:
                    code = response.status_code
                    body = b""
                    for chunk in response.iter_bytes(8 * 1024):
                        body += chunk
                        if len(body) >= MAX_BODY_BYTES:
                            break
        except httpx.TimeoutException:
            return "CONNECTION_FAILED", "timeout"
        except httpx.HTTPError:
            return "CONNECTION_FAILED", "unreachable"
        return _classify(code, body[:MAX_BODY_BYTES])

    def head_metadata(self, key: str) -> "ObjectHead":
        """HEAD of one metadata object: its size, type and modification time, never its content.

        The key must be a root metadata file of a Sentinel-2 L2A product (PRODUCT_KEY_RE). A HEAD answer has no body,
        and none is read, so no object content can be transferred. Never raises for a network fault."""
        if not PRODUCT_KEY_RE.match(key):
            raise ValueError("not a Sentinel-2 L2A root metadata object")
        path = f"/{BUCKET}/{key}"
        headers = sign_get(
            method="HEAD",
            host=_host_header(self._endpoint),
            path=path,
            query={},
            access_key=self._access,
            secret_key=self._secret,
            region=self._region,
            now=self._now,
        )
        headers["User-Agent"] = USER_AGENT
        url = f"{self._endpoint}{encode_path(path)}"
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=httpx.Timeout(self._timeout, connect=min(5.0, self._timeout)),
                follow_redirects=False,
            ) as http:
                with http.stream("HEAD", url, headers=headers) as response:  # the body is never iterated
                    return _classify_head(response.status_code, response.headers)
        except httpx.TimeoutException:
            return ObjectHead("CONNECTION_FAILED", "timeout", None)
        except httpx.HTTPError:
            return ObjectHead("CONNECTION_FAILED", "unreachable", None)


@dataclass(frozen=True)
class ObjectHead:
    """A sanitised HEAD result: a status, a reason, the HTTP code and a few validated header values. Nothing else."""

    status: str  # ACCESSIBLE | NOT_FOUND | ACCESS_DENIED | CONNECTION_FAILED, or why nothing was sent
    reason: str
    http_status: int | None
    metadata: dict[str, str | int] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", HEAD_MESSAGES.get(self.reason) or MESSAGES[self.reason])


HEAD_MESSAGES = {
    "accessible": "The object exists and the account may read its metadata. No content was transferred.",
    "not_found": "The store has no such object (404). It may have been removed or the path is wrong.",
    "access_denied": "The store refused access to this object (401/403); the credentials were accepted for a listing.",
    "moved": "The store answered with a redirect, which is not followed. Check the endpoint.",
    "unexpected_response": "The answer was not what an S3 object HEAD returns.",
}
_HTTP_DATE_RE = re.compile(r"^[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT$")
_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+\-]{0,60}/[a-z0-9][a-z0-9!#$&^_.+\-]{0,60}$")
_ETAG_RE = re.compile(r"^[A-Za-z0-9\-]{8,80}$")


def _classify_head(code: int, headers: httpx.Headers) -> ObjectHead:
    if code == 200:
        length = headers.get("content-length", "")
        if not length.isdigit():
            return ObjectHead("CONNECTION_FAILED", "unexpected_response", code)
        found: dict[str, str | int] = {"content_length": int(length)}
        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if _TYPE_RE.match(content_type):
            found["content_type"] = content_type
        modified = headers.get("last-modified", "").strip()
        if _HTTP_DATE_RE.match(modified):
            found["last_modified"] = modified
        etag = headers.get("etag", "").strip().strip('"')
        if _ETAG_RE.match(etag):
            found["etag"] = etag
        return ObjectHead("ACCESSIBLE", "accessible", code, found)
    if code == 404:
        return ObjectHead("NOT_FOUND", "not_found", code)
    if code in (401, 403):
        return ObjectHead("ACCESS_DENIED", "access_denied", code)
    if code == 429:
        return ObjectHead("CONNECTION_FAILED", "rate_limited", code)
    if 300 <= code < 400:
        return ObjectHead("CONNECTION_FAILED", "moved", code)
    if code >= 500:
        return ObjectHead("CONNECTION_FAILED", "server_error", code)
    return ObjectHead("CONNECTION_FAILED", "unexpected_response", code)


def head_request_summary(settings: CdseS3Settings, key: str) -> dict[str, object]:
    """Exactly what a HEAD of `key` sends, for the confirmation shown before it. No credential is included."""
    if not PRODUCT_KEY_RE.match(key):
        raise ValueError("not a Sentinel-2 L2A root metadata object")
    endpoint = _clean_endpoint(settings.endpoint) or "(invalid)"
    path = encode_path(f"/{BUCKET}/{key}")
    return {
        "method": "HEAD",
        "host": urlsplit(endpoint).netloc,
        "path": path,
        "url": f"{endpoint}{path}",
        "signed_with": "AWS Signature V4; the access key id is in the request, the secret key never is",
        "body": "none",
        "sends_project_or_area_data": False,
    }


def head_object(
    settings: CdseS3Settings,
    key: str,
    *,
    online_enabled: bool,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> ObjectHead:
    """HEAD one product metadata object, unless configuration already rules it out (nothing is then sent)."""
    if not PRODUCT_KEY_RE.match(key):
        raise ValueError("not a Sentinel-2 L2A root metadata object")
    blocked = _local_problem(settings, online_enabled)
    if blocked is not None:
        return ObjectHead(blocked.status, blocked.reason, None)
    secrets = [
        settings.access_key.get_secret_value() if settings.access_key else None,
        settings.secret_key.get_secret_value() if settings.secret_key else None,
    ]
    try:
        result = CdseS3Client(settings, transport=transport, now=now).head_metadata(key)
    except Exception as exc:  # a fault here must not leak through a message that might hold a secret
        logger.warning("cdse-s3 head failed internally: %s", redact(type(exc).__name__, secrets))
        result = ObjectHead("CONNECTION_FAILED", "unreachable", None)
    logger.info(
        "cdse-s3 head: status=%s reason=%s http=%s host=%s",
        result.status,
        result.reason,
        result.http_status,
        urlsplit(settings.endpoint).hostname,
    )
    return result


def _classify(code: int, body: bytes) -> tuple[str, str]:
    if code == 200:
        if b"<ListBucketResult" in body:
            return "AUTHENTICATED", "authenticated"
        return "CONNECTION_FAILED", "unexpected_response"
    found = _CODE_RE.search(body)
    known = _STORE_CODES.get(found.group(1).decode()) if found else None
    if known:
        return known
    if code in (401, 403):
        return "AUTHENTICATION_FAILED", "rejected"
    if code == 429:
        return "CONNECTION_FAILED", "rate_limited"
    if 300 <= code < 400:
        return "CONNECTION_FAILED", "unexpected_redirect"
    if code >= 500:
        return "CONNECTION_FAILED", "server_error"
    return "CONNECTION_FAILED", "unexpected_response"


def verify(
    settings: CdseS3Settings,
    *,
    online_enabled: bool,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> S3Status:
    """Check the credentials with one signed read-only request, unless configuration already rules it out.

    Disabled, gated and credential-less states return without any network use."""
    blocked = _local_problem(settings, online_enabled)
    if blocked is not None:
        return blocked
    secrets = [
        settings.access_key.get_secret_value() if settings.access_key else None,
        settings.secret_key.get_secret_value() if settings.secret_key else None,
    ]
    try:
        status, reason = CdseS3Client(settings, transport=transport, now=now).probe()
    except Exception as exc:  # a fault in this code must not leak through a message that might hold a secret
        logger.warning("cdse-s3 verify failed internally: %s", redact(type(exc).__name__, secrets))
        status, reason = "CONNECTION_FAILED", "unreachable"
    logger.info("cdse-s3 verify: status=%s reason=%s host=%s", status, reason, urlsplit(settings.endpoint).hostname)
    return _status(settings, online_enabled, status, reason, datetime.now(UTC))
