"""Notion REST helpers: one request funnel, structured errors, pagination.

API version is pinned to NOTION_VERSION below. That pin matters more than usual
here: `2025-09-03` split databases into a *container* (`/v1/databases`) plus one
or more *data sources* (`/v1/data_sources`), and `2026-03-11` renamed
`archived` -> `in_trash` and replaced the flat `after` parameter on
append-block-children with a `position` object. Mixing shapes across versions
fails in ways that are hard to read, so every call here speaks one version.

Nothing in this module puts a token into a message, a log line or an error.
"""

from __future__ import annotations

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"

# Notion caps every paginated endpoint at 100 items per request.
MAX_PAGE_SIZE = 100

# --- structured error codes (I-EXT-ERROR-CODE-NORMALIZED) -------------------
# Every error that reaches the user carries a stable code: it is what the
# platform error taxonomy, self-diagnosis and honest narration key on. An
# error emitted without one is stamped EXT_UNSTRUCTURED_ERROR at the dispatch
# boundary, which degrades the user's diagnosis to prose parsing.
#
# Platform taxonomy codes (imperal_sdk.chat.error_codes) are reused where the
# meaning matches exactly: PERMISSION_DENIED, RATE_LIMITED, BACKEND_5XX,
# BACKEND_TIMEOUT. Everything Notion-specific gets an app-declared code
# matching ^[A-Z][A-Z0-9_]{2,63}$. The code never appears in the message prose
# -- the two travel as separate fields.
NOTION_TOKEN_MISSING = "NOTION_TOKEN_MISSING"
NOTION_TOKEN_REJECTED = "NOTION_TOKEN_REJECTED"
NOTION_NOT_SHARED = "NOTION_NOT_SHARED"
NOTION_VALIDATION_FAILED = "NOTION_VALIDATION_FAILED"
NOTION_CONFLICT = "NOTION_CONFLICT"
NOTION_UNREACHABLE = "NOTION_UNREACHABLE"
NOTION_RESPONSE_NOT_JSON = "NOTION_RESPONSE_NOT_JSON"
NOTION_RESPONSE_UNEXPECTED = "NOTION_RESPONSE_UNEXPECTED"
NOTION_HTTP_ERROR = "NOTION_HTTP_ERROR"
NOTION_WORKSPACE_UNKNOWN = "NOTION_WORKSPACE_UNKNOWN"
NOTION_TARGET_NOT_FOUND = "NOTION_TARGET_NOT_FOUND"
NOTION_TARGET_AMBIGUOUS = "NOTION_TARGET_AMBIGUOUS"
NOTION_CAPABILITY_MISSING = "NOTION_CAPABILITY_MISSING"
NOTION_NO_DATA_SOURCE = "NOTION_NO_DATA_SOURCE"

# Notion's own `code` field is more precise than the HTTP status, so it wins.
# `object_not_found` is the interesting one: Notion returns it both for "no such
# id" AND for "exists but not shared with this integration" -- the integration
# genuinely cannot tell those apart. The connector says so instead of guessing.
_NOTION_CODE_MAP = {
    "unauthorized": NOTION_TOKEN_REJECTED,
    "restricted_resource": "PERMISSION_DENIED",
    "insufficient_permissions": NOTION_CAPABILITY_MISSING,
    "object_not_found": NOTION_NOT_SHARED,
    "validation_error": NOTION_VALIDATION_FAILED,
    "invalid_json": NOTION_VALIDATION_FAILED,
    "invalid_request_url": NOTION_VALIDATION_FAILED,
    "invalid_request": NOTION_VALIDATION_FAILED,
    "missing_version": NOTION_VALIDATION_FAILED,
    "conflict_error": NOTION_CONFLICT,
    "rate_limited": "RATE_LIMITED",
    "internal_server_error": "BACKEND_5XX",
    "service_unavailable": "BACKEND_5XX",
    "service_overload": "BACKEND_5XX",
    "gateway_timeout": "BACKEND_TIMEOUT",
    "database_connection_unavailable": "BACKEND_5XX",
}

_MESSAGES = {
    NOTION_TOKEN_REJECTED: (
        "Notion rejected the integration token -- it may have been revoked or "
        "pasted incompletely. Add a fresh Internal Integration Secret."
    ),
    NOTION_NOT_SHARED: (
        "Notion can't see that item. Either it doesn't exist, or it hasn't been "
        "shared with the integration yet -- open it in Notion, then use the "
        "page's Connections menu to add your integration."
    ),
    "PERMISSION_DENIED": (
        "The integration isn't allowed to touch that item. Check its "
        "capabilities in the Notion integration settings."
    ),
    NOTION_CAPABILITY_MISSING: (
        "The integration lacks the capability this action needs (for example "
        "insert or update content). Enable it in the Notion integration settings."
    ),
    NOTION_VALIDATION_FAILED: "Notion rejected the request as invalid.",
    NOTION_CONFLICT: (
        "Notion reported a conflicting edit -- that item changed while this "
        "request was in flight. Try again."
    ),
    "RATE_LIMITED": "Notion is rate-limiting requests -- try again shortly.",
    "BACKEND_5XX": "Notion returned a server error -- try again shortly.",
    "BACKEND_TIMEOUT": "Notion took too long to respond -- try again shortly.",
    NOTION_UNREACHABLE: "Could not reach the Notion API.",
}

_RETRYABLE = {"RATE_LIMITED", "BACKEND_5XX", "BACKEND_TIMEOUT",
              NOTION_CONFLICT, NOTION_UNREACHABLE}


def is_retryable(code: str) -> bool:
    """Whether retrying the identical call could plausibly succeed."""
    return code in _RETRYABLE


def message_for(code: str) -> str:
    """User-facing text for a structured code (prose and code stay separate)."""
    return _MESSAGES.get(code, "The Notion request failed.")


def auth_headers(token: str) -> dict:
    """Auth + version headers. The token is never logged by this module."""
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def transport_error_code(exc: BaseException) -> str:
    """Classify a transport-level failure talking to Notion.

    A timeout is a distinct, retryable condition with its own taxonomy code --
    worth separating from "host does not resolve / refused the connection",
    because the useful next step differs.
    """
    name = type(exc).__name__.lower()
    if "timeout" in name or "timedout" in name:
        return "BACKEND_TIMEOUT"
    return NOTION_UNREACHABLE


def classify(status_code: int, body) -> tuple[str, str]:
    """Map a failed Notion response onto (code, user-facing message)."""
    notion_code = ""
    detail = ""
    if isinstance(body, dict):
        notion_code = str(body.get("code") or "")
        detail = str(body.get("message") or "")

    code = _NOTION_CODE_MAP.get(notion_code, "")
    if not code:
        if status_code == 401:
            code = NOTION_TOKEN_REJECTED
        elif status_code == 403:
            code = "PERMISSION_DENIED"
        elif status_code == 404:
            code = NOTION_NOT_SHARED
        elif status_code == 409:
            code = NOTION_CONFLICT
        elif status_code == 429:
            code = "RATE_LIMITED"
        elif 500 <= status_code < 600:
            code = "BACKEND_5XX"
        else:
            code = NOTION_HTTP_ERROR

    message = _MESSAGES.get(code) or f"Notion request failed (HTTP {status_code})."
    # Notion's own message is echoed ONLY for validation errors: there it names
    # the offending field, which is exactly what makes the failure fixable. It
    # is not echoed for auth failures, where the curated explanation is better
    # and the raw text adds nothing actionable.
    if code == NOTION_VALIDATION_FAILED and detail:
        message = f"Notion rejected the request: {detail}"
    return code, message


def fail(code: str, error: str = "") -> dict:
    """Build the module's error envelope with a stable code."""
    return {"ok": False, "code": code, "retryable": is_retryable(code),
            "error": error or message_for(code)}


async def request(ctx, method: str, path: str, token: str, *,
                  json: dict | None = None, params: dict | None = None,
                  timeout: int = 30) -> dict:
    """Call one Notion endpoint.

    Returns {"ok": True, "data": dict} or {"ok": False, "error", "code",
    "retryable"}. Every Notion call in this app funnels through here, so
    classification, timeouts and the version pin cannot drift between sites.
    """
    if not token:
        return fail(NOTION_TOKEN_MISSING,
                    "No Notion integration token is configured yet -- add one in "
                    "the app's Secrets tab.")

    url = f"{NOTION_API}/{path.lstrip('/')}"
    fn = getattr(ctx.http, method.lower())
    kwargs: dict = {"headers": auth_headers(token), "timeout": timeout}
    if json is not None:
        kwargs["json"] = json
    if params:
        kwargs["params"] = params

    try:
        # Explicit timeout: a hanging call must fail as a diagnosable in-handler
        # exception, not hang until the platform cancels the coroutine (which
        # surfaces to the user as an opaque INTERNAL).
        resp = await fn(url, **kwargs)
    except Exception as e:
        # The exception TYPE is a useful fact (DNS vs refused vs timeout); the
        # raw exception string is not -- it can carry hosts and internal paths.
        return fail(transport_error_code(e))

    body = resp.body
    if isinstance(body, (str, bytes, bytearray)) and body:
        try:
            body = resp.json()
        except Exception:
            if resp.status_code >= 400:
                code, message = classify(resp.status_code, None)
                return {"ok": False, "code": code, "error": message,
                        "retryable": is_retryable(code)}
            return fail(NOTION_RESPONSE_NOT_JSON,
                        "Notion returned a success status but the response body "
                        "wasn't valid JSON.")

    if resp.status_code >= 400:
        code, message = classify(resp.status_code, body)
        return {"ok": False, "code": code, "error": message,
                "retryable": is_retryable(code)}

    if not isinstance(body, dict):
        return fail(NOTION_RESPONSE_UNEXPECTED,
                    "Notion returned an unexpected response shape.")

    return {"ok": True, "data": body}


async def paginate(ctx, method: str, path: str, token: str, *,
                   json: dict | None = None, params: dict | None = None,
                   limit: int = MAX_PAGE_SIZE, max_pages: int = 10) -> dict:
    """Follow Notion's cursor pagination until `limit` items or `max_pages`.

    Returns {"ok": True, "results": list, "has_more": bool} or the same error
    envelope as `request`. `max_pages` is a hard stop so one tool call on a huge
    workspace can never turn into an unbounded crawl.
    """
    results: list = []
    cursor: str | None = None
    has_more = False

    for _ in range(max_pages):
        want = min(MAX_PAGE_SIZE, max(1, limit - len(results)))
        if method.upper() == "GET":
            page_params = dict(params or {})
            page_params["page_size"] = want
            if cursor:
                page_params["start_cursor"] = cursor
            out = await request(ctx, "GET", path, token, params=page_params)
        else:
            payload = dict(json or {})
            payload["page_size"] = want
            if cursor:
                payload["start_cursor"] = cursor
            out = await request(ctx, method, path, token, json=payload)

        if not out.get("ok"):
            return out

        data = out["data"]
        batch = data.get("results")
        if not isinstance(batch, list):
            return fail(NOTION_RESPONSE_UNEXPECTED,
                        "Notion returned a list response without results.")
        results.extend(batch)
        has_more = bool(data.get("has_more"))
        cursor = data.get("next_cursor") or None
        if len(results) >= limit or not has_more or not cursor:
            break

    return {"ok": True, "results": results[:limit], "has_more": has_more}
