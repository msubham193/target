"""Why Earth Engine refused to run. Every refusal has a stable code and an HTTP status; every message is redacted."""

from geopoc.satellite.gate import ProviderError, redact

# code -> HTTP status for the API. A refusal because of the feature's current state (switched off, not configured,
# stopped by the kill switch, a missing control folder or log, a missing package, another process holding the lock) is
# 409: the request conflicts with the state of the feature, and no input can make the server fail. 5xx is not used for
# these. 502 is kept for a real failure reported by Google.
STATUS: dict[str, int] = {
    "EE_DISABLED": 409,
    "EE_NOT_CONFIGURED": 409,
    "EE_KILL_SWITCH": 409,
    "EE_CONTROL_UNAVAILABLE": 409,
    "EE_LOG_UNAVAILABLE": 409,
    "EE_PACKAGE_MISSING": 409,
    "EE_INSTANCE_CONFLICT": 409,
    "EE_DEVELOPER_ONLY": 403,
    "EE_PHASE_NOT_ENABLED": 403,
    "EE_BUDGET_EXCEEDED": 429,
    "EE_AUTH_FAILED": 502,
    "EE_UPSTREAM": 502,
    "EE_UNKNOWN_DATASET": 404,
}


class EarthEngineRefused(ProviderError):
    """Earth Engine was not used. The message never carries a token, key or signed URL (it is redacted on creation)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(redact(message))
        self.code = code

    @property
    def status(self) -> int:
        return STATUS.get(self.code, 409)
