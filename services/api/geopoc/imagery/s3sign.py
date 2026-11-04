"""AWS Signature Version 4 for a body-less S3 GET or HEAD, using only the standard library.

S3-compatible stores such as the Copernicus Data Space Ecosystem accept SigV4. This signs exactly one kind of request, a
GET or HEAD with no body, so there is no payload to hash and no chunking. It never makes a connection and never logs:
it returns the headers to send. The secret key is used to derive the signing key and is not part of the output.

Checked against the example in AWS's published S3 documentation (tests/test_cdse_s3.py).
"""

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
SIGNED_HEADERS = "host;x-amz-content-sha256;x-amz-date"


def encode_path(path: str) -> str:
    """The path with each segment percent-encoded once, as S3 requires (S3 does not encode twice)."""
    return quote(path or "/", safe="/-_.~")


def encode_query(query: dict[str, str]) -> str:
    """The canonical query string: names and values percent-encoded, sorted by name then value."""
    pairs = sorted((quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in query.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sign_get(
    *,
    method: str = "GET",
    host: str,
    path: str,
    query: dict[str, str],
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime | None = None,
    service: str = "s3",
) -> dict[str, str]:
    """The headers (x-amz-date, x-amz-content-sha256, Authorization) that authorise `{method} https://{host}{path}?{query}`.

    `host` must be exactly what the HTTP client will send in its Host header (the port only when not the default)."""
    if method not in ("GET", "HEAD"):
        raise ValueError("only body-less GET and HEAD requests are signed")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    canonical_request = "\n".join(
        [
            method,
            encode_path(path),
            encode_query(query),
            f"host:{host}\nx-amz-content-sha256:{EMPTY_SHA256}\nx-amz-date:{amz_date}\n",
            SIGNED_HEADERS,
            EMPTY_SHA256,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "x-amz-date": amz_date,
        "x-amz-content-sha256": EMPTY_SHA256,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={SIGNED_HEADERS}, Signature={signature}"
        ),
    }
