"""Storing a token.

The bug these cover: saving reported success while the app still read nothing
back, so the user pasted a key, watched the field clear, and had a dead app.
Two properties matter and are asserted directly against the store's contents:

* a token Notion REJECTS must leave the store untouched -- never persist a
  credential already known to be broken;
* a token Notion ACCEPTS must be readable by the very next read.
"""

import pytest
from imperal_sdk.testing import MockSecretStore

import notion_client as nc
import workspaces as ws
from conftest import bot_payload_default

SECRET = "notion_tokens"


def _stored(ctx) -> str:
    """Whatever is actually in the secret store right now."""
    return ctx.secrets._store.get(SECRET, "")


# --- happy path -------------------------------------------------------------

async def test_a_valid_token_is_stored_and_immediately_readable(ctx, http):
    """The regression in one test: saved must mean visible."""
    http.push(bot_payload_default(workspace="Acme HQ"))

    out = await ws.add_token(ctx, "ntn_good_token")

    assert out["ok"] is True
    assert out["already_connected"] is False
    assert out["workspace_name"] == "Acme HQ"
    assert out["count"] == 1

    # The read path -- the one that used to come back empty -- now sees it.
    assert await ws.load_tokens(ctx) == ["ntn_good_token"]


async def test_the_token_is_verified_before_it_is_written(ctx, http):
    """Notion is asked FIRST; /users/me is what identifies the workspace."""
    http.push(bot_payload_default())
    await ws.add_token(ctx, "ntn_good_token")
    assert http.urls() == ["https://api.notion.com/v1/users/me"]


async def test_whitespace_around_a_pasted_token_is_forgiven(ctx, http):
    """Copy-paste picks up spaces and newlines; that must not break a save."""
    http.push(bot_payload_default())
    await ws.add_token(ctx, "  ntn_good_token\n")
    assert _stored(ctx) == "ntn_good_token"


# --- rejection: nothing may be persisted ------------------------------------

async def test_a_rejected_token_is_not_stored_at_all(ctx, http):
    """A bad paste must not leave a broken credential behind."""
    http.push({"code": "unauthorized", "message": "API token is invalid."}, 401)

    out = await ws.add_token(ctx, "ntn_wrong")

    assert out["ok"] is False
    assert out["code"] == nc.NOTION_TOKEN_REJECTED
    assert SECRET not in ctx.secrets._store, "nothing may be written"


async def test_a_rejected_token_does_not_disturb_an_existing_one(connected_ctx, http):
    """Adding a bad second token must not damage a working first one."""
    http.push({"code": "unauthorized", "message": "invalid"}, 401)

    out = await ws.add_token(connected_ctx, "ntn_wrong")

    assert out["ok"] is False
    assert _stored(connected_ctx) == "ntn_test_token_one"


async def test_an_empty_submission_is_refused_without_calling_notion(ctx, http):
    out = await ws.add_token(ctx, "   ")
    assert out["ok"] is False
    assert out["code"] == nc.NOTION_TOKEN_MISSING
    assert http.calls == [], "no network call for an empty field"


# --- multiple workspaces ----------------------------------------------------

async def test_a_second_token_is_appended_not_replaced(connected_ctx, http):
    """One integration == one workspace, so workspace #2 is a second line."""
    http.push(bot_payload_default(workspace="Side Project"))

    out = await ws.add_token(connected_ctx, "ntn_test_token_two")

    assert out["ok"] is True
    assert out["count"] == 2
    assert _stored(connected_ctx) == "ntn_test_token_one\nntn_test_token_two"
    assert await ws.load_tokens(connected_ctx) == [
        "ntn_test_token_one", "ntn_test_token_two",
    ]


async def test_pasting_the_same_token_twice_changes_nothing(connected_ctx, http):
    """Idempotent: a double submit must not create a duplicate workspace."""
    http.push(bot_payload_default())

    out = await ws.add_token(connected_ctx, "ntn_test_token_one")

    assert out["ok"] is True
    assert out["already_connected"] is True
    assert _stored(connected_ctx) == "ntn_test_token_one"


async def test_exceeding_the_size_limit_is_refused_before_writing(ctx, http):
    """max_bytes is enforced here so the failure is explainable, not a 4xx."""
    # 64 lines x 64 bytes ~= 4160 bytes: already at the ceiling, so ONE more
    # token must be refused. (60 lines was under the limit and proved nothing.)
    filler = "\n".join(f"ntn_{i:0>60}" for i in range(64))
    ctx.secrets = MockSecretStore({SECRET: filler})
    before = _stored(ctx)
    http.push(bot_payload_default())

    out = await ws.add_token(ctx, "ntn_one_token_too_many")

    assert out["ok"] is False
    assert out["code"] == nc.NOTION_VALIDATION_FAILED
    assert _stored(ctx) == before, "the stored value must be untouched"


# --- storage failures must not masquerade as "not configured" ---------------

async def test_an_unreadable_store_is_reported_as_such_not_as_missing(ctx):
    """The old code turned ANY read failure into "no token configured"."""
    class Broken:
        async def get(self, name):
            raise RuntimeError("vault down")

    ctx.secrets = Broken()

    out = await ws.read_tokens(ctx)

    assert out["ok"] is False
    assert out["code"] == nc.NOTION_SECRET_UNAVAILABLE
    assert "RuntimeError" in out["error"]


async def test_a_failed_write_is_reported_and_never_silent(ctx, http):
    http.push(bot_payload_default())

    class WriteFails(MockSecretStore):
        async def set(self, name, value):
            raise RuntimeError("vault down")

    ctx.secrets = WriteFails({})

    out = await ws.add_token(ctx, "ntn_good_token")

    assert out["ok"] is False
    assert out["code"] == nc.NOTION_SECRET_WRITE_FAILED


async def test_no_error_message_ever_contains_the_token(ctx, http):
    """Federal rule: plaintext must never ride along in an error."""
    http.push({"code": "unauthorized", "message": "invalid"}, 401)
    secret = "ntn_super_secret_value"

    out = await ws.add_token(ctx, secret)

    assert secret not in str(out)


# --- the tool the form submits to -------------------------------------------
#
# The panel form and chat both call this, so its RESULT is what the user
# actually reads. Silence was the original complaint, so the summary has to say
# what happened.

async def test_connect_workspace_tool_reports_which_workspace_it_connected(ctx, http):
    import handlers_write as hw
    from models import ConnectWorkspaceParams

    http.push(bot_payload_default(workspace="Acme HQ"))

    result = await hw.connect_workspace(ctx, ConnectWorkspaceParams(token="ntn_ok"))

    assert result.status == "success"
    assert "Acme HQ" in result.summary
    # Panels showing connection state must not keep saying "not connected".
    assert "connect" in (result.refresh_panels or [])


async def test_connect_workspace_tool_explains_sharing_after_connecting(ctx, http):
    """A fresh integration sees nothing until pages are shared -- say so now,
    not after the user reports an empty search."""
    import handlers_write as hw
    from models import ConnectWorkspaceParams

    http.push(bot_payload_default())
    result = await hw.connect_workspace(ctx, ConnectWorkspaceParams(token="ntn_ok"))
    assert "Connections" in result.summary


async def test_connect_workspace_tool_surfaces_a_rejection_with_its_code(ctx, http):
    import handlers_write as hw
    from models import ConnectWorkspaceParams

    http.push({"code": "unauthorized", "message": "invalid"}, 401)

    result = await hw.connect_workspace(ctx, ConnectWorkspaceParams(token="ntn_bad"))

    assert result.status == "error"
    # the field is `error_code`, not `code`
    assert result.error_code == nc.NOTION_TOKEN_REJECTED
