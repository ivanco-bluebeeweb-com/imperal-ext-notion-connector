"""Panel rendering.

These did not exist before, which is why two real bugs shipped: the center
panel unpacked `list_workspaces` (a LIST) into two values, so it fell into its
own except branch on every load, and the table read keys the records never had.
Rendering each panel against real fixtures is what catches that class of bug.
"""

import json

from imperal_sdk.testing import MockSecretStore

import panels
from conftest import bot_payload_default


def _flatten(node) -> list:
    """Every UINode in the tree, depth-first."""
    out = []
    if node is None:
        return out
    props = getattr(node, "props", None)
    if props is None:
        return out
    out.append(node)
    for value in props.values():
        if isinstance(value, list):
            for item in value:
                out.extend(_flatten(item))
        else:
            out.extend(_flatten(value))
    return out


def _types(node) -> list[str]:
    return [n.type for n in _flatten(node)]


def _dump(node) -> str:
    """Serialised tree — used to assert on text without walking by hand."""
    return json.dumps(node, default=lambda o: getattr(o, "props", str(o)))


# --- the connect screen -----------------------------------------------------

async def test_connect_screen_offers_a_masked_field_not_a_plain_input(ctx):
    """ui.Password is the required credential surface (EXT-SECRETS-V1).

    ui.Password is a thin wrapper that returns an Input node carrying
    type="password", so the masking — not the node name — is the contract.
    """
    tree = await panels.connect_panel(ctx)
    fields = [n for n in _flatten(tree) if n.type == "Input"]
    assert len(fields) == 1, "exactly one credential field"
    assert fields[0].props["type"] == "password"
    assert fields[0].props["param_name"] == "value"


async def test_connect_form_posts_to_the_platform_secret_action(ctx):
    """Extension code cannot write a write_mode='user' secret itself."""
    tree = await panels.connect_panel(ctx)
    form = next(n for n in _flatten(tree) if n.type == "Form")
    assert form.props["action"] == "save_app_secret"
    assert form.props["defaults"] == {
        "app_id": "notion-connector",
        "name": "notion_tokens",
    }


async def test_connect_screen_tells_the_user_which_integration_type(ctx):
    """The redirect-URI confusion comes from picking a PUBLIC integration."""
    body = _dump(await panels.connect_panel(ctx))
    assert "INTERNAL" in body
    assert "redirect URI" in body


async def test_connect_screen_explains_sharing_since_a_fresh_token_sees_nothing(ctx):
    body = _dump(await panels.connect_panel(ctx))
    assert "Connections" in body


async def test_connect_warns_that_saving_replaces_existing_tokens(connected_ctx, http):
    """save_app_secret overwrites the whole value — silence would lose tokens."""
    http.push(bot_payload_default())
    body = _dump(await panels.connect_panel(connected_ctx))
    assert "replaces" in body


async def test_connect_screen_never_renders_a_token_back(connected_ctx, http):
    http.push(bot_payload_default())
    tree = await panels.connect_panel(connected_ctx)
    pwd = next(n for n in _flatten(tree) if n.type == "Input")
    assert not pwd.props.get("value"), "the field must never be pre-filled"
    assert "ntn_test_token_one" not in _dump(tree)


# --- the workspaces panel ---------------------------------------------------

async def test_workspaces_panel_renders_connected_rows(connected_ctx, http):
    """The regression: this used to hit its except branch every single time."""
    http.push(bot_payload_default(workspace="Acme HQ"))
    tree = await panels.workspaces_panel(connected_ctx)

    table = next(n for n in _flatten(tree) if n.type == "DataTable")
    assert table.props["rows"] == [{
        "workspace": "Acme HQ",
        "integration": "Imperal",
        "status": "Ready",
    }]

    alert = next(n for n in _flatten(tree) if n.type == "Alert")
    assert alert.props["type"] == "success"


async def test_workspaces_panel_row_keys_match_the_declared_columns(connected_ctx, http):
    """Rows keyed differently from columns render as blank cells."""
    http.push(bot_payload_default())
    tree = await panels.workspaces_panel(connected_ctx)
    table = next(n for n in _flatten(tree) if n.type == "DataTable")
    column_keys = {c["key"] for c in table.props["columns"]}
    for row in table.props["rows"]:
        assert set(row) == column_keys


async def test_empty_workspaces_panel_leads_to_the_connect_screen(ctx):
    """A first-time user must be able to get somewhere from the empty state."""
    tree = await panels.workspaces_panel(ctx)
    empty = next(n for n in _flatten(tree) if n.type == "Empty")
    action = empty.props["action"]
    assert action.params["function"] == "__panel__connect"


async def test_a_broken_token_is_a_warning_row_not_a_blank_screen(ctx, http):
    ctx.secrets = MockSecretStore({"notion_tokens": "bad\ngood"})
    http.push({"code": "unauthorized", "message": "invalid"}, 401)
    http.push(bot_payload_default(workspace="Good WS"))

    tree = await panels.workspaces_panel(ctx, refresh=True)
    alert = next(n for n in _flatten(tree) if n.type == "Alert")
    assert alert.props["type"] == "warn"
    table = next(n for n in _flatten(tree) if n.type == "DataTable")
    assert len(table.props["rows"]) == 2
    assert "Needs attention" in [r["status"] for r in table.props["rows"]]


async def test_panel_renders_a_banner_when_loading_blows_up(ctx, monkeypatch):
    """A blank screen is worse than a banner — and no internals leak."""
    async def boom(*_a, **_k):
        raise RuntimeError("store exploded: /internal/path")

    monkeypatch.setattr(panels.ws, "list_workspaces", boom)
    tree = await panels.workspaces_panel(ctx)
    body = _dump(tree)
    assert "store exploded" not in body
    assert "/internal/path" not in body
    alert = next(n for n in _flatten(tree) if n.type == "Alert")
    assert alert.props["type"] == "error"


# --- the sidebar ------------------------------------------------------------

async def test_sidebar_offers_connect_when_nothing_is_configured(ctx):
    tree = await panels.notion_nav(ctx)
    labels = [n.props.get("label") for n in _flatten(tree) if n.type == "Button"]
    assert "Connect Notion" in labels


async def test_sidebar_switches_to_open_panel_once_connected(connected_ctx, http):
    http.push(bot_payload_default())
    tree = await panels.notion_nav(connected_ctx)
    labels = [n.props.get("label") for n in _flatten(tree) if n.type == "Button"]
    assert "Open Notion panel" in labels
    assert "Connect Notion" not in labels
