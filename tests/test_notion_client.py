"""The request funnel: error classification, pagination, and what never leaks."""

import pytest

import notion_client as nc
from conftest import list_payload


# --- error classification ---------------------------------------------------

def test_notion_error_code_wins_over_the_http_status():
    """Notion's own `code` is more precise than the status it arrives with."""
    code, _ = nc.classify(404, {"code": "object_not_found", "message": "..."})
    assert code == nc.NOTION_NOT_SHARED


@pytest.mark.parametrize("status,expected", [
    (401, nc.NOTION_TOKEN_REJECTED),
    (403, "PERMISSION_DENIED"),
    (404, nc.NOTION_NOT_SHARED),
    (409, nc.NOTION_CONFLICT),
    (429, "RATE_LIMITED"),
    (500, "BACKEND_5XX"),
    (503, "BACKEND_5XX"),
])
def test_each_http_status_maps_to_a_stable_code(status, expected):
    code, message = nc.classify(status, None)
    assert code == expected
    assert message


def test_validation_errors_echo_notions_detail_because_it_names_the_field():
    _, message = nc.classify(400, {"code": "validation_error",
                                    "message": "body.properties.Status is not a select"})
    assert "Status" in message


def test_auth_failures_do_not_echo_notions_raw_text():
    """For 401 the curated sentence is better; the raw text adds nothing."""
    _, message = nc.classify(401, {"code": "unauthorized",
                                    "message": "API token is invalid."})
    assert "API token is invalid." not in message
    assert message


def test_timeout_is_distinguished_from_unreachable():
    class ConnectTimeout(Exception):
        pass

    class ConnectError(Exception):
        pass

    assert nc.transport_error_code(ConnectTimeout()) == "BACKEND_TIMEOUT"
    assert nc.transport_error_code(ConnectError()) == nc.NOTION_UNREACHABLE


def test_retryable_flag_matches_the_kind_of_failure():
    assert nc.is_retryable("RATE_LIMITED") is True
    assert nc.is_retryable("BACKEND_5XX") is True
    assert nc.is_retryable(nc.NOTION_TOKEN_REJECTED) is False
    assert nc.is_retryable(nc.NOTION_NOT_SHARED) is False


def test_every_declared_code_has_a_message():
    """A code with no message would surface as an empty error to the user."""
    codes = [v for k, v in vars(nc).items()
             if k.startswith("NOTION_") and isinstance(v, str) and k.isupper()
             and k not in ("NOTION_API", "NOTION_VERSION")]
    for code in codes:
        assert nc.message_for(code), code


# --- the request funnel -----------------------------------------------------

async def test_missing_token_fails_before_any_http_call(ctx, http):
    out = await nc.request(ctx, "GET", "users/me", "")
    assert out["ok"] is False
    assert out["code"] == nc.NOTION_TOKEN_MISSING
    assert http.calls == []


async def test_version_header_is_pinned_on_every_request(ctx, http):
    http.push({"object": "user", "id": "u1"})
    await nc.request(ctx, "GET", "users/me", "tok")
    assert http.calls[0]["headers"]["Notion-Version"] == nc.NOTION_VERSION
    assert nc.NOTION_VERSION == "2026-03-11"


async def test_token_never_appears_in_an_error_message(ctx, http):
    """The single most important leak to prevent."""
    secret = "ntn_super_secret_value"
    http.push({"code": "unauthorized", "message": f"token {secret} rejected"}, 401)
    out = await nc.request(ctx, "GET", "users/me", secret)
    assert out["ok"] is False
    assert secret not in out["error"]


async def test_transport_exception_message_is_not_forwarded(ctx, http):
    http.push(RuntimeError("connect to 10.0.0.5:443 failed (internal path /etc/x)"))
    out = await nc.request(ctx, "GET", "users/me", "tok")
    assert out["ok"] is False
    assert "10.0.0.5" not in out["error"]
    assert out["code"] == nc.NOTION_UNREACHABLE


async def test_non_json_success_body_is_reported_not_silently_accepted(ctx, http):
    http.push("<html>maintenance</html>", 200)
    out = await nc.request(ctx, "GET", "users/me", "tok")
    assert out["ok"] is False
    assert out["code"] == nc.NOTION_RESPONSE_NOT_JSON


# --- pagination -------------------------------------------------------------

async def test_pagination_follows_the_cursor_and_concatenates(ctx, http):
    http.push(list_payload([{"id": "1"}, {"id": "2"}], has_more=True, cursor="c1"))
    http.push(list_payload([{"id": "3"}], has_more=False))
    out = await nc.paginate(ctx, "POST", "search", "tok", limit=100)
    assert out["ok"] is True
    assert [r["id"] for r in out["results"]] == ["1", "2", "3"]
    assert http.calls[1]["json"]["start_cursor"] == "c1"


async def test_pagination_stops_at_the_requested_limit(ctx, http):
    http.push(list_payload([{"id": str(i)} for i in range(100)],
                            has_more=True, cursor="c1"))
    out = await nc.paginate(ctx, "POST", "search", "tok", limit=5)
    assert len(out["results"]) == 5
    assert len(http.calls) == 1


async def test_pagination_surfaces_a_mid_crawl_error(ctx, http):
    http.push(list_payload([{"id": "1"}], has_more=True, cursor="c1"))
    http.push({"code": "rate_limited", "message": "slow down"}, 429)
    out = await nc.paginate(ctx, "POST", "search", "tok", limit=100)
    assert out["ok"] is False
    assert out["code"] == "RATE_LIMITED"
    assert out["retryable"] is True
