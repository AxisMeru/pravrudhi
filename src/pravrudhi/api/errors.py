"""Stable error codes for 503 responses (pravrudhi#318): a machine-readable `error` code and a FIXED human `detail`.

Exception text never goes into a response, a streamed event or a stored job: the caller logs it server-side
(`logger.warning(..., exc_info=True)`) and returns one of these. `detail` is kept beside `error` so existing clients that
read `detail` keep working.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

AGENT_AT_CAPACITY = "agent_at_capacity"
AGENT_UNAVAILABLE = "agent_unavailable"
CHECKER_UNAVAILABLE = "checker_unavailable"
REGISTRY_CHECKER_UNAVAILABLE = "registry_checker_unavailable"
CHAT_ENDPOINT_UNREACHABLE = "chat_endpoint_unreachable"
VENDOR_NOT_ALLOWED = "vendor_not_allowed"
REQUEST_REJECTED = "request_rejected"
UNKNOWN_CONTRACT = "unknown_contract"

#: code -> the one fixed message that goes with it.
MESSAGES: dict[str, str] = {
    AGENT_AT_CAPACITY: "the nyaya agent is at capacity; retry shortly",
    AGENT_UNAVAILABLE: "the nyaya agent is unavailable; retry later",
    CHECKER_UNAVAILABLE: "the checker is unavailable; retry later",
    REGISTRY_CHECKER_UNAVAILABLE: "the registry checker is unavailable; retry later",
    CHAT_ENDPOINT_UNREACHABLE: "the chat model endpoint is unreachable; retry later",
    VENDOR_NOT_ALLOWED: "vendor not allowed for API callers",
    REQUEST_REJECTED: "the analysis engine rejected the request",
    UNKNOWN_CONTRACT: "one or more of the requested contracts or sections is not known",
}


def coded(status_code: int, code: str) -> JSONResponse:
    """A response with the stable `code` and its fixed message. An unknown code is a programming error, not a free-text escape."""
    return JSONResponse(status_code=status_code, content={"error": code, "detail": MESSAGES[code]})


def coded_503(code: str) -> JSONResponse:
    return coded(503, code)
