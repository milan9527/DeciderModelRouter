"""Bounded recovery for explicit Bedrock transient errors, never ambiguous timeouts."""

import math
import random
import time
from email.utils import parsedate_to_datetime

from botocore.exceptions import ClientError

RETRYABLE = {
    "ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException",
    "InternalServerException",
}
MAX_ENDPOINTS = 3
MAX_ENDPOINT_ATTEMPTS = 2
MAX_ATTEMPTS = MAX_ENDPOINTS * MAX_ENDPOINT_ATTEMPTS
TOTAL_TIMEOUT_SECONDS = 180
MIN_ATTEMPT_SECONDS = 1
COOLDOWN_SECONDS = 60


def error_details(exc):
    cause = exc.__cause__
    response = cause.response if isinstance(cause, ClientError) else {}
    metadata = response.get("ResponseMetadata", {})
    headers = {k.lower(): v for k, v in metadata.get("HTTPHeaders", {}).items()}
    value = headers.get("retry-after")
    retry_after = None
    if value is not None:
        try:
            retry_after = float(value)
        except (TypeError, ValueError):
            try:
                retry_after = parsedate_to_datetime(value).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                pass
        if retry_after is not None:
            retry_after = max(0, retry_after) if math.isfinite(retry_after * 1000) else None
    return {
        "error_code": response.get("Error", {}).get("Code") or str(exc.status_code),
        "aws_request_id": metadata.get("RequestId"),
        "upstream_status_code": metadata.get("HTTPStatusCode"),
        "retry_after_ms": round(retry_after * 1000, 3) if retry_after is not None else None,
    }


def retry_delay(number, details):
    """Equal jitter prevents tight loops; an upstream Retry-After is a lower bound."""
    ceiling = min(8, 2 ** (number - 1))
    return max(random.uniform(ceiling / 2, ceiling), (details["retry_after_ms"] or 0) / 1000)
