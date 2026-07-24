"""Multi-workspace handling and name-first target resolution."""

from imperal_sdk.testing import MockSecretStore

import notion_client as nc
import workspaces as ws
from conftest import list_payload, page_payload


def bot_payload(name="Imperal", workspace="Acme HQ", bot_id="bot-1"):
    return {"object": "user", "id": bot_id, "name": name, "type": "bot",
            "bot": {"workspace_name": workspace, "workspace_id": "w-1",
                     "owner": {"type": "workspace"}}}


# --- tokens -----------------------------------------------------------------

async def test_one_token_per_line_becomes_one_workspace_each(ctx):
    ctx.secrets = MockSecretStore({"notion_tokens": "tok_a\ntok_b\n"})
    assert await ws.load_tokens(ctx) == ["tok_a", "tok_b"]


async def test_blank_lines_and_duplicates_do_not_create_phantom_workspaces(ctx):
    ctx.secrets = MockSecretStore({"notion_tokens": "  tok_a  \n\n\ntok_a\n  \n"})
    assert await ws.load_tokens(ctx) == ["tok_a"]


async def test_no_secret_configured_is_empty_not_an_exception(ctx):
    ctx.secrets = MockSecretStore({})
    assert await ws.load_tokens(ctx) == []


# --- describing workspaces --------------------------------------------------

async def test_workspace_name_comes_from_the_bot_token_itself(ctx, http):
    http.push(bot_payload(workspace="Acme HQ"))
    info = await ws.describe_token(ctx, "tok")
    assert info["ok"] is True
    assert info["workspace_name"] == "Acme HQ"


async def test_one_broken_token_does_not_blank_out_the_others(ctx, http):
    """A revoked token must degrade to a describable row, not an exception."""
    ctx.secrets = MockSecretStore({"notion_tokens": "bad\ngood"})
    http.push({"code": "unauthorized", "message": "invalid"}, 401)
    http.push(bot_payload(workspace="Good WS"))

    entries = await ws.list_workspaces(ctx, refresh=True)
    assert len(entries) == 2
    assert entries[0]["status"] == "error"
    assert entries[0]["code"] == nc.NOTION_TOKEN_REJECTED
    assert entries[1]["workspace_name"] == "Good WS"


async def test_a_stored_workspace_record_never_contains_the_token(ctx, http):
    """The store is not Vault — a token there would be a real leak."""
    ctx.secrets = MockSecretStore({"notion_tokens": "ntn_secret_abc"})
    http.push(bot_payload())
    await ws.list_workspaces(ctx, refresh=True)

    page = await ctx.store.query(ws.WORKSPACES_COLLECTION, limit=100)
    blob = str([doc.data for doc in page.data])
    assert "ntn_secret_abc" not in blob


# --- picking a workspace ----------------------------------------------------

async def test_single_workspace_needs_no_name(ctx, http):
    ctx.secrets = MockSecretStore({"notion_tokens": "tok"})
    http.push(bot_payload(workspace="Only One"))
    picked = await ws.resolve_workspace(ctx, "")
    assert picked["ok"] is True
    assert picked["token"] == "tok"


async def test_naming_a_workspace_selects_it_case_insensitively(ctx, http):
    ctx.secrets = MockSecretStore({"notion_tokens": "tok_a\ntok_b"})
    http.push(bot_payload(workspace="Acme HQ"))
    http.push(bot_payload(workspace="Side Project"))
    picked = await ws.resolve_workspace(ctx, "side project")
    assert picked["ok"] is True
    # The name maps back to the RIGHT token — slot order must be preserved.
    assert picked["token"] == "tok_b"


async def test_unknown_workspace_name_is_a_structured_error(ctx, http):
    ctx.secrets = MockSecretStore({"notion_tokens": "tok"})
    http.push(bot_payload(workspace="Acme HQ"))
    picked = await ws.resolve_workspace(ctx, "Nope")
    assert picked["ok"] is False
    assert picked["code"] == nc.NOTION_WORKSPACE_UNKNOWN
    # The error NAMES what is connected, so the user can correct themselves.
    assert "Acme HQ" in picked["error"]


async def test_no_tokens_at_all_points_the_user_at_setup(ctx):
    ctx.secrets = MockSecretStore({})
    picked = await ws.resolve_workspace(ctx, "")
    assert picked["ok"] is False
    assert picked["code"] == nc.NOTION_TOKEN_MISSING


async def test_several_workspaces_and_no_name_refuses_to_guess(ctx, http):
    """Picking one at random and then WRITING to it is unrecoverable."""
    ctx.secrets = MockSecretStore({"notion_tokens": "tok_a\ntok_b"})
    http.push(bot_payload(workspace="Acme HQ"))
    http.push(bot_payload(workspace="Side Project"))
    picked = await ws.resolve_workspace(ctx, "")
    assert picked["ok"] is False
    assert picked["code"] == nc.NOTION_WORKSPACE_UNKNOWN
    assert "Acme HQ" in picked["error"] and "Side Project" in picked["error"]


# --- name-first target resolution -------------------------------------------

def test_id_detection_accepts_dashed_and_undashed_uuids():
    assert ws.looks_like_id("11111111111111111111111111111111")
    assert ws.looks_like_id("11111111-1111-1111-1111-111111111111")
    assert not ws.looks_like_id("Q3 Roadmap")
    assert not ws.looks_like_id("")


async def test_a_pasted_id_skips_the_search_entirely(ctx, http):
    out = await ws.resolve_target(ctx, "tok", "11111111111111111111111111111111")
    assert out["ok"] is True
    assert out["resolved_by"] == "id"
    assert http.calls == []


async def test_a_name_is_resolved_by_search(ctx, http):
    http.push(list_payload([page_payload(title="Q3 Roadmap")]))
    out = await ws.resolve_target(ctx, "tok", "Q3 Roadmap")
    assert out["ok"] is True
    assert out["title"] == "Q3 Roadmap"
    assert out["resolved_by"] == "name"


async def test_ambiguous_name_refuses_to_guess(ctx, http):
    """Guessing here could mean writing to the wrong page."""
    http.push(list_payload([
        page_payload(page_id="a" * 32, title="Roadmap"),
        page_payload(page_id="b" * 32, title="Roadmap"),
    ]))
    out = await ws.resolve_target(ctx, "tok", "Roadmap")
    assert out["ok"] is False
    assert out["code"] == nc.NOTION_TARGET_AMBIGUOUS


async def test_an_exact_title_match_wins_over_partial_matches(ctx, http):
    http.push(list_payload([
        page_payload(page_id="a" * 32, title="Roadmap 2026 draft"),
        page_payload(page_id="b" * 32, title="Roadmap"),
    ]))
    out = await ws.resolve_target(ctx, "tok", "Roadmap")
    assert out["ok"] is True
    assert out["id"] == "b" * 32


async def test_nothing_found_explains_sharing_rather_than_saying_missing(ctx, http):
    http.push(list_payload([]))
    out = await ws.resolve_target(ctx, "tok", "Ghost Page")
    assert out["ok"] is False
    assert out["code"] == nc.NOTION_TARGET_NOT_FOUND
    assert "share" in out["error"].lower()
